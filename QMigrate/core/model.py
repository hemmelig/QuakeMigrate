# -*- coding: utf-8 -*-
"""
Module to produce gridded traveltime velocity models

"""

import math
import warnings
import pickle
import struct
from copy import copy
import os

import skfmm
import pyproj
import numpy as np
import matplotlib
from scipy.interpolate import RegularGridInterpolator, griddata, interp1d
try:
    os.environ["DISPLAY"]
    matplotlib.use('Qt5Agg')
except KeyError:
    matplotlib.use('Agg')
import matplotlib.pylab as plt


def _cart2sph_np_array(xyz):
    # theta_phi_r = _cart2sph_np_array(xyz)
    tpr = np.zeros(xyz.shape)
    xy = xyz[:, 0] ** 2 + xyz[:, 1] ** 2
    tpr[:, 0] = np.arctan2(xyz[:, 1], xyz[:, 0])
    tpr[:, 1] = np.arctan2(xyz[:, 2], np.sqrt(xy))
    tpr[:, 2] = np.sqrt(xy + xyz[:, 2] ** 2)
    return tpr


def _cart2sph_np(xyz):
    # theta_phi_r = _cart2sph_np(xyz)
    if xyz.ndim == 1:
        tpr = np.zeros(3)
        xy = xyz[0] ** 2 + xyz[1] ** 2
        tpr[0] = np.arctan2(xyz[1], xyz[0])
        tpr[1] = np.arctan2(xyz[2], np.sqrt(xy))
        tpr[2] = np.sqrt(xy + xyz[2] ** 2)
    else:
        tpr = np.zeros(xyz.shape)
        xy = xyz[:, 0] ** 2 + xyz[:, 1] ** 2
        tpr[:, 0] = np.arctan2(xyz[:, 1], xyz[:, 0])
        tpr[:, 1] = np.arctan2(xyz[:, 2], np.sqrt(xy))
        tpr[:, 2] = np.sqrt(xy + xyz[:, 2] ** 2)
    return tpr


def _sph2cart_np(tpr):
    # xyz = _sph2cart_np(theta_phi_r)
    if tpr.ndim == 1:
        xyz = np.zeros(3)
        xyz[0] = tpr[2] * np.cos(tpr[1]) * np.cos(tpr[0])
        xyz[1] = tpr[2] * np.cos(tpr[1]) * np.sin(tpr[0])
        xyz[2] = tpr[2] * np.sin(tpr[1])
    else:
        xyz = np.zeros(tpr.shape)
        xyz[:, 0] = tpr[:, 2] * np.cos(tpr[:, 1]) * np.cos(tpr[:, 0])
        xyz[:, 1] = tpr[:, 2] * np.cos(tpr[:, 1]) * np.sin(tpr[:, 0])
        xyz[:, 2] = tpr[:, 2] * np.sin(tpr[:, 1])
    return xyz


def _coord_transform_np(p1, p2, loc):
    xyz = np.zeros(loc.shape)
    if loc.ndim == 1:
        xyz[0], xyz[1], xyz[2] = pyproj.transform(p1, p2,
                                                  loc[0],
                                                  loc[1],
                                                  loc[2])
    else:
        xyz[:, 0], xyz[:, 1], xyz[:, 2] = pyproj.transform(p1, p2,
                                                           loc[:, 0],
                                                           loc[:, 1],
                                                           loc[:, 2])
    return xyz


def _proj(**kwargs):
    projection = kwargs.get("projection")
    if projection == "WGS84":
        proj = "+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs"  # "+init=EPSG:4326"
    if projection == "NAD27":
        proj = "+proj=longlat +ellps=clrk66 +datum=NAD27 +no_defs"  # "+init=EPSG:4267"
    if projection == "UTM":
        zone = _utm_zone(kwargs.get("longitude"))
        proj = "+proj=utm +zone={0:d} +datum=WGS84 +units=m +no_defs"
        proj = proj.format(zone)
    if projection == "LCC":
        lon0 = kwargs.get("lon0")
        lat0 = kwargs.get("lat0")
        parallel_1 = kwargs.get("parallel_1")
        parallel_2 = kwargs.get("parallel_2")
        proj = "+proj=lcc +lon_0={} +lat_0={} +lat_1={} +lat_2={} +datum=WGS84 +units=m +no_defs"
        proj = proj.format(float(lon0), float(lat0),
                           float(parallel_1), float(parallel_2))
    if projection == "TM":
        lon = kwargs.get("lon")
        lat = kwargs.get("lat")
        proj = "+proj=tmerc +lon_0={} +lat_0={} +datum=WGS84 +units=m +no_defs"
        proj = proj.format(float(lon), float(lat))

    return pyproj.Proj(proj)


def _utm_zone(longitude):
    return (int(1 + math.fmod((longitude + 180.0) / 6.0, 60)))


# def _proj_nlloc_simple(latOrg,lonOrg,rotAngle):
#     x = (long - longOrig) * 111.111 * cos(lat_radians)
#     y = (lat - latOrig) * 111.111
#     lat = latOrig + y / 111.111
#     long = longOrig + x / (111.111 * cos(lat_radians))
#     x=(lon)


def eikonal(ix, iy, iz, dxi, dyi, dzi, V, S):
    """
    Travel-Time formulation using a simple eikonal method.

    Requires the skifmm python package.

    Parameters
    ----------
    ix : array-like
        Number of cells in X-direction
    iy : array-like
        Number of cells in Y-direction
    iz : array-like
        Number of cells in Z-direction
    dxi :
        Cell length in X-direction
    dyi :
        Cell length in Y-direction
    dzi :
        Cell length in Z-direction
    V : array-like
        Contains the speed of interface propagation at each point in the domain
    S : array-like
        ???

    Returns
    -------
    t : array-like, same shape as phi
        Contains the travel time from the zero contour (zero level set) of phi
        to each point in the array given the scalar velocity field speed. If
        the input array speed has values less than or equal to zero the return
        value will be a masked array.

    """

    phi = -np.ones(ix.shape)
    indx = np.argmin(abs((ix - S[:, 0]))
                     + abs((iy - S[:, 1]))
                     + abs((iz - S[:, 2])))
    phi[np.unravel_index(indx, ix.shape)] = 1.0

    t = skfmm.travel_time(phi, V, dx=[dxi, dyi, dzi])
    return t


class Grid3D(object):
    """
    3D grid class

    Attributes
    ----------
    cell_count : array-like
        Number of cells in each dimension of the grid
    cell_size : array-like
        Size of a cell in each dimension of the grid
    azimuth : float
        Angle between northing vertical plane and grid y-z plane
    dip : float
        Angle between horizontal plane and grid x-y plane

    Methods
    -------
    lonlat_centre(longitude, latitude)
        Define the longitude and latitude of the centre of the grid
    nlloc_grid_centre(origin_lon, origin_lat)
        Define the centre of the grid from NonLinLoc file parameters

    """

    def __init__(self, cell_count, cell_size, azimuth, dip, sort_order="C"):
        """
        Class initialisation

        Parameters
        ----------
        cell_count : array-like
            Number of cells in each dimension of the grid
        cell_size : array-like
            Size of a cell in each dimension of the grid
        azimuth : float
            Angle between northing vertical plane and grid y-z plane
        dip : float
            Angle between horizontal plane and grid x-y plane
        sort_order : str
            Determines whether the multi-index should be viewed as indexing in
            row-major (C-style) or column-major (Fortran-style) order.
        longitude : float
            Longitude coordinate of the grid centre
        latitude : float
            Latitude coordinate of the grid centre
        elevation : float
            Elevation coordinate of the grid centre (units: m)
        grid_centre : array-like
            Array containing coordinates of the grid centre
        grid_proj : pyproj object
            Grid space projection
        coord_proj : pyproj object
            Coordinate space projection

        """

        self._coord_proj = None
        self._grid_proj = None
        self._longitude = None
        self._latitude = None
        self._grid_centre = [0.0, 0.0, 0.0]

        self.cell_count = cell_count
        self.cell_size = cell_size
        self.elevation = (cell_count[2] * cell_size[2] / -2)
        self.azimuth = azimuth
        self.dip = dip
        self.sort_order = sort_order
        self.UTM_zones_different = False
        self.lcc_standard_parallels = (0.0, 0.0)

    def projections(self, grid_proj, coord_proj=None):
        if coord_proj and self._coord_proj is None:
            self.coord_proj = _proj(projection=coord_proj)
        elif self._coord_proj is None:
            self.coord_proj = _proj(projection="WGS84")

        if grid_proj == "UTM":
            self.grid_proj = _proj(projection=grid_proj,
                                   longitude=self.longitude)
        elif grid_proj == "LCC":
            self.grid_proj = _proj(projection=grid_proj, lon0=self.longitude,
                                   lat0=self.latitude,
                                   parallel_1=self.lcc_standard_parallels[0],
                                   parallel_2=self.lcc_standard_parallels[1])
        elif grid_proj == "TM":
            self.grid_proj = _proj(projection=grid_proj, lon=self.longitude,
                                   lat=self.latitude)
        else:
            msg = "Projection type must be specified.\n"
            msg += "SeisLoc currently supports:\n"
            msg += "        UTM\n"
            msg += "        LCC (Lambert Conical Conformic)\n"
            msg += "        TM (Transverse Mercator"
            raise Exception(msg)

    def lonlat_centre(self, longitude=None, latitude=None):
        """
        Define the centre of the 3D grid in geographical coordinates

        Parameters
        ----------
        longitude : float
            Geographical longitude of grid centre
        latitude : float
            Geographical latitude of grid centre

        """

        if longitude:
            self.longitude = longitude
        if latitude:
            self.latitude = latitude

    def nlloc_grid_centre(self, origin_lon, origin_lat):
        """

        Parameters
        ----------
        origin_lon : float
            Geographical longitude of grid origin
        origin_lat : float
            Geographical latitude of grid origin

        """

        self.coord_proj = _proj(projection="WGS84")
        # if _utm_zone(self.longitude) != _utm_zone(origin_lon):
        #     self.grid_proj = _proj(projection="UTM", longitude=self.longitude)
        if self.NLLoc_proj != "NONE":
            self.grid_proj = self._nlloc_grid_proj()
        grid_origin = self.xy2lonlat(origin_lon, origin_lat, inverse=True)
        x = grid_origin[0] + self.centre[0]
        y = grid_origin[1] + self.centre[1]
        self.longitude, self.latitude = self.xy2lonlat(x, y)
        self._update_grid_centre()

    def _update_grid_centre(self):
        x, y = pyproj.transform(self.coord_proj, self.grid_proj,
                                self.longitude, self.latitude)

        self.grid_centre = [x, y, self.elevation]

    def _update_coord_centre(self):
        lon, lat = pyproj.transform(self.coord_proj, self.grid_proj,
                                    self.grid_centre[0], self.grid_centre[1])
        self.longitude = lon
        self.latitude = lat

    def _nlloc_grid_proj(self):
        if self.NLLoc_proj:
            if self.NLLoc_proj == "SIMPLE":
                print("ERROR -- simple not yet supported")
            elif self.NLLoc_proj == "LAMBERT":
                return _proj(projection="LCC", lon0=self.NLLoc_MapOrg[0],
                             lat0=self.NLLoc_MapOrg[1],
                             parallel_1=self.NLLoc_MapOrg[4],
                             parallel_2=self.NLLoc_MapOrg[5])
            elif self.NLLoc_proj == "TRANS_MERC":
                return _proj(projection="TM", lon=self.NLLoc_MapOrg[0],
                             lat=self.NLLoc_MapOrg[1])

    @property
    def cell_count(self):
        """Get and set the number of cells in each dimension of the grid."""

        return self._cell_count

    @cell_count.setter
    def cell_count(self, value):
        value = np.array(value, dtype='int32')
        if value.size == 1:
            value = np.repeat(value, 3)
        else:
            assert (value.shape == (3,)), "Cell count must be an n by 3 array."
        assert (np.all(value > 0)), "Cell count must be greater than [0]"
        self._cell_count = value

    @property
    def cell_size(self):
        """
        Get and set the size of a cell in each dimension of the grid.

        """

        return self._cell_size

    @cell_size.setter
    def cell_size(self, value):
        value = np.array(value, dtype='float64')
        if value.size == 1:
            value = np.repeat(value, 3)
        else:
            assert (value.shape == (3,)), "Cell size must be an n by 3 array."
        assert (np.all(value > 0)), "Cell size must be greater than [0]"
        self._cell_size = value

    @property
    def longitude(self):
        """Get and set the longitude of the grid centre"""

        return self._longitude

    @longitude.setter
    def longitude(self, value):
        # Add tests for suitable longitude
        self._longitude = value
        if self._grid_proj and self.coord_proj and self.latitude:
            self._update_grid_centre()

    @property
    def latitude(self):
        """Get and set the latitude of the grid centre"""

        return self._latitude

    @latitude.setter
    def latitude(self, value):
        # Add tests for suitable latitude
        self._latitude = value
        if self._grid_proj and self.coord_proj and self.longitude:
            self._update_grid_centre()

    @property
    def elevation(self):
        """
        Get the elevation of the grid centre

        """

        return self._elevation

    @elevation.setter
    def elevation(self, value):
        # Add tests for suitable elevation
        self._elevation = value
        if (self._grid_proj and self._coord_proj and
                self.longitude and self.latitude):
            self._update_grid_centre()

    @property
    def grid_centre(self):
        """Get and set the centre of the grid"""
        # x, y = pyproj.transform(self.grid_proj, self.coord_proj,
        #                         self.longitude, self.latitude)
        # self._grid_centre = [x, y, self.elevation]

        return self._grid_centre

    @grid_centre.setter
    def grid_centre(self, value):
        value = np.array(value, dtype='float64')
        assert (value.shape == (3,)), 'Grid centre must be [x, y, z] array.'
        self._grid_centre = value

    @property
    def grid_proj(self):
        """
        Get and set the grid projection (defaults to WGS84)

        """

        if self._grid_proj is None:
            msg = "Grid projection has not been set: assuming WGS84"
            warnings.warn(msg)
            return _proj(projection="UTM", longitude=self.longitude)
        else:
            return self._grid_proj

    @grid_proj.setter
    def grid_proj(self, value):
        self._grid_proj = value
        if self._coord_proj and self.longitude and self.latitude:
            self._update_grid_centre()

    @property
    def coord_proj(self):
        """Get and set the coordinate projection"""
        return self._coord_proj

    @coord_proj.setter
    def coord_proj(self, value):
        self._coord_proj = value
        if self._grid_proj and self.longitude and self.latitude:
            self._update_coord_centre()

    def xy2lonlat(self, x, y, inverse=False):
        x = np.array(x)
        y = np.array(y)
        if inverse:
            return pyproj.transform(self.coord_proj,
                                    self.grid_proj,
                                    x, y)
        else:
            return pyproj.transform(self.grid_proj,
                                    self.coord_proj,
                                    x, y)

    def local2global(self, value, inverse=False):
        tpr = _cart2sph_np(value - self.grid_centre)
        if inverse:
            tpr -= [self.azimuth, self.dip, 0.0]
        else:
            tpr += [self.azimuth, self.dip, 0.0]
        return (_sph2cart_np(tpr) + self.grid_centre)

    def xyz2loc(self, value, inverse=False):
        if inverse:
            return self.local2global(self.grid_centre
                                     + (self.cell_size
                                        * (value - (self.cell_count - 1) / 2)))
        else:
            return ((self.local2global(value, inverse=True) - self.grid_centre)
                    / self.cell_size) + (self.cell_count - 1) / 2

    def index2loc(self, value, inverse=False):
        if inverse:
            return np.ravel_multi_index(value, self.cell_count, mode='clip',
                                        order=self.sort_order)
        else:
            out = np.vstack(np.unravel_index(value, self.cell_count,
                                             order=self.sort_order))
            return out.transpose()

    def xyz2index(self, value, inverse=False):
        if inverse:
            return self.xyz2loc(self.index2loc(value), inverse=True)
        else:
            return self.index2loc(self.xyz2loc(value), inverse=True)

    def xyz2coord(self, value, inverse=False):
        if inverse:
            x, y = self.xy2lonlat(value[:, 0], value[:, 1], inverse=True)
            z = value[:, 2]

            corners = self.grid_corners
            xmin, ymin, zmin = np.min(corners, axis=0)
            xmax, ymax, zmax = np.max(corners, axis=0)

            if x < xmin:
                x = np.array([xmin + self.cell_size[0] / 2])
            if x > xmax:
                x = np.array([xmax - self.cell_size[0] / 2])
            if y < ymin:
                y = np.array([ymin + self.cell_size[1] / 2])
            if y > ymax:
                y = np.array([ymax - self.cell_size[1] / 2])
            if z < zmin:
                z = np.array([zmin + self.cell_size[2] / 2])
            if z > zmax:
                z = np.array([zmax - self.cell_size[2] / 2])

            return np.array([x, y, z]).transpose()
        else:
            lon, lat = self.xy2lonlat(value[:, 0], value[:, 1])
            return np.array([lon, lat, value[:, 2]]).transpose()

    def coord2loc(self, value, inverse=False):
        if inverse:
            return self.xyz2coord(self.xyz2loc(value, inverse=True))
        else:
            return self.xyz2loc(self.xyz2coord(value, inverse=True))

    def coord2index(self, value, inverse=False):
        if inverse:
            return self.coord2loc(self.index2loc(value), inverse=True)
        else:
            return self.index2loc(self.coord2loc(value), inverse=True)

    @property
    def grid_origin(self):
        grid_size = self.cell_count * self.cell_size
        return self.local2global(self.grid_centre - (grid_size / 2))

    @property
    def grid_corners(self):
        """
        Get the xyz positions of the cells on the edge of the grid

        """

        lc = self.cell_count - 1
        ly, lx, lz = np.meshgrid([0, lc[1]], [0, lc[0]], [0, lc[2]])
        loc = np.c_[lx.flatten(), ly.flatten(), lz.flatten()]
        return self.xyz2loc(loc, inverse=True)

    @property
    def grid_xyz(self):
        """
        Get the xyz positions of all of the cells in the grid

        """

        lc = self.cell_count
        ly, lx, lz = np.meshgrid(np.arange(lc[1]),
                                 np.arange(lc[0]),
                                 np.arange(lc[2]))
        loc = np.c_[lx.flatten(), ly.flatten(), lz.flatten()]
        coord = self.xyz2loc(loc, inverse=True)
        lx = coord[:, 0].reshape(lc)
        ly = coord[:, 1].reshape(lc)
        lz = coord[:, 2].reshape(lc)
        return lx, ly, lz


class NonLinLoc:
    """
    NonLinLoc class

    Reading and manipulating NLLoc Grids in a 2D or 3D format

    Attributes
    ----------

    Methods
    -------
    nlloc_load_file(filename)
        Parses information from .hdr and .buf files into NonLinLoc variables

    TO-DO
    -----
    Loading of 2D travel-times


    """

    def __init__(self):
        self.NLLoc_n = np.array([0, 0, 0])
        self.NLLoc_org = np.array([0, 0, 0])
        self.NLLoc_siz = np.array([0, 0, 0])
        self.NLLoc_type = "TIME"
        self.NLLoc_proj = "NONE"
        # Has form lon - lat - rotation - reference ellipsoid - std1 - std2
        self.NLLoc_MapOrg = [0.0, 0.0, 0.0, "SIMPLE", 0.0, 0.0]
        self.NLLoc_data = None

    def nlloc_load_file(self, filename):
        """
        Parse information from .hdr and .buf files into NonLinLoc variables

        Parameters
        ----------
        filename : str
            File name (not including extension)

        """

        # Read the .hdr file
        f = open('{}.hdr'.format(filename), 'r')

        # Defining the grid dimensions
        params = f.readline().split()
        self.NLLoc_n = np.array([int(params[0]),
                                 int(params[1]),
                                 int(params[2])])
        self.NLLoc_org = np.array([float(params[3]),
                                   float(params[4]),
                                   float(params[5])])
        self.NLLoc_siz = np.array([float(params[6]),
                                   float(params[7]),
                                   float(params[8])])
        self.NLLoc_type = params[9]

        # Defining the station information
        stats = f.readline().split()
        del stats

        # Defining the transform information
        trans = f.readline().split()
        if trans[1] == "NONE":
            self.NLLoc_proj = "NONE"
        if trans[1] == "SIMPLE":
            self.NLLoc_proj = "SIMPLE"
            self.NLLoc_MapOrg = [trans[5], trans[3], trans[7],
                                 "SIMPLE", '0.0', '0.0']
        if trans[1] == "LAMBERT":
            self.NLLoc_proj = "LAMBERT"
            self.NLLoc_MapOrg = [trans[7], trans[5], trans[13],
                                 trans[3], trans[9], trans[11]]
        if trans[1] == "TRANS_MERC":
            self.NLLoc_proj = "TRANS_MERC"
            self.NLLoc_MapOrg = [trans[7], trans[5], trans[9],
                                 trans[3], '0.0', '0.0']

        # Reading the .buf file
        fid = open('{}.buf'.format(filename), 'rb')
        data = struct.unpack('{}f'.format(self.NLLoc_n[0]
                                          * self.NLLoc_n[1]
                                          * self.NLLoc_n[2]),
                             fid.read(self.NLLoc_n[0]
                                      * self.NLLoc_n[1]
                                      * self.NLLoc_n[2] * 4))
        self.NLLoc_data = np.array(data).reshape(self.NLLoc_n[0],
                                                 self.NLLoc_n[1],
                                                 self.NLLoc_n[2])

    def nlloc_project_grid(self):
        """
        Projecting the grid to the new coordinate system.

        This function also determines the 3D grid from the 2D grids from
        NonLinLoc

        """

        # Generating the correct NonLinLoc Formatted Grid
        if self.NLLoc_proj == "NONE":
            GRID_NLLOC = Grid3D(cell_count=self.NLLoc_n,
                                cell_size=self.NLLoc_siz,
                                azimuth=0.0,
                                dip=0.0)
            GRID_NLLOC.nlloc_grid_centre(self.NLLoc_org[0], self.NLLoc_org[1])
        else:
            GRID_NLLOC = Grid3D(cell_count=self.NLLoc_n,
                                cell_size=self.NLLoc_siz,
                                azimuth=self.NLLoc_MapOrg[2],
                                dip=0.0)
            GRID_NLLOC.lonlat_centre(self.NLLoc_MapOrg[0],
                                     self.NLLoc_MapOrg[1])

        # TO-DO: What is the text in NLLoc_MapOrg[3]?
        if self.NLLoc_proj == "LAMBERT":
            GRID_NLLOC.projections(grid_proj=self.NLLoc_MapOrg[3])

        if self.NLLoc_proj == "TRANS_MERC":
            GRID_NLLOC.projections(grid_proj=self.NLLoc_MapOrg[3])

        OrgX, OrgY, OrgZ = GRID_NLLOC.grid_xyz
        NewX, NewY, NewZ = self.grid_xyz

        self.NLLoc_data = griddata((OrgX.flatten(),
                                    OrgY.flatten(),
                                    OrgZ.flatten()),
                                   self.NLLoc_data.flatten(),
                                   (NewX, NewY, NewZ),
                                   method='nearest')

    def nlloc_regrid(self, decimate):
        """
        Redefining coordinate system to the file loaded

        Parameters
        ----------
        decimate : 


        """

        centre = self.NLLoc_org + self.NLLoc_size * (self.NLLoc_n - 1) / 2
        self.centre = centre * [1000, 1000, -1000]
        self.cell_count = self.NLLoc_n
        self.cell_size = self.NLLoc_siz * 1000
        self.dip = 0.0

        if self.NLLoc_proj == "NONE":
            self.azimuth = 0.0
            self.grid_centre = self.centre

        if self.NLLoc_proj == "SIMPLE":
            self.azimuth = self.NLLoc_MapOrg[2]
            self.nlloc_grid_centre(float(self.NLLoc_MapOrg[0]),
                                   float(self.NLLoc_MapOrg[1]))
            self.elevation = self.centre[2]

        if self.NLLoc_proj == "LAMBERT":
            self.azimuth = float(self.NLLoc_MapOrg[2])
            self.nlloc_grid_centre(float(self.NLLoc_MapOrg[0]),
                                   float(self.NLLoc_MapOrg[1]))
            self.elevation = self.centre[2]

        if self.NLLoc_proj == "TRANS_MERC":
            self.azimuth = float(self.NLLoc_MapOrg[2])
            self.nlloc_grid_centre(float(self.NLLoc_MapOrg[0]),
                                   float(self.NLLoc_MapOrg[1]))
            self.elevation = self.centre[2]

        self.NLLoc_data = self.decimate_array(self.NLLoc_data,
                                              np.array(decimate))[:, :, ::-1]


class LUT(Grid3D, NonLinLoc):
    """
    Look-Up Table class

    Inherits from Grid3D and NonLinLoc classes

    Attributes
    ----------
    maps : dict
        Contains traveltime tables for P- and S-phases.

    Methods
    -------
    stations(path, units, delimiter=",")
        Read in station files
    station_xyz(station=None)
        Returns the xyz position of a specific station relative to the origin
        (default returns all locations)
    decimate(ds, inplace=False)
        Downsample the initial velocity model tables that are loaded before
        processing


    TO-DO
    -----
    Weighting of the stations with distance (allow the user to define their own
    tables or define a fixed weighting for the problem)
    Move maps from being stored in RAM (use JSON or HDF5)


        _select_station - Selecting the stations to be used in the LUT
        set_station     - Defining the station locations to be used

    """

    def __init__(self, cell_count=[51, 51, 31], cell_size=[30.0, 30.0, 30.0],
                 azimuth=0.0, dip=0.0):
        """
        Class initialisation method

        Parameters
        ----------
        cell_count : array-like
            Number of cells in each dimension of the grid
        cell_size : array-like
            Size of a cell in each dimension of the grid
        azimuth : float
            Angle between northing vertical plane and grid y-z plane
        dip : float
            Angle between horizontal plane and grid x-y plane

        """

        Grid3D.__init__(self, cell_count, cell_size, azimuth, dip)
        NonLinLoc.__init__(self)

        self.velocity_model = None
        self.station_data = None
        self._maps = {}
        self.data = None

    def stations(self, path, units, delimiter=","):
        """
        Reads station information from file

        Parameters
        ----------
        path : str
            Location of file containing station information
        delimiter : char, optional
            Station file delimiter, defaults to ","
        units : str

        """

        import pandas as pd

        stats = pd.read_csv(path, delimiter=delimiter).values

        stn_data = {}
        if units == 'offset':
            stn_lon, stn_lat = self.xy2lonlat(stats[:, 0].astype('float')
                                              + self.grid_centre[0],
                                              stats[:, 1].astype('float')
                                              + self.grid_centre[1])
        elif units == 'xyz':
            stn_lon, stn_lat = self.xy2lonlat(stats[:, 0], stats[:, 1])
        elif units == 'lon_lat_elev':
            stn_lon = stats[:, 0]
            stn_lat = stats[:, 1]
        elif units == 'lat_lon_elev':
            stn_lon = stats[:, 1]
            stn_lat = stats[:, 0]

        stn_data['Longitude'] = stn_lon
        stn_data['Latitude'] = stn_lat
        stn_data['Elevation'] = stats[:, 2]
        stn_data['Name'] = stats[:, 3]

        self.station_data = stn_data

    def station_xyz(self, station=None):
        if station is None:
            stn = self.station_data
        else:
            station = self._select_station(station)
            stn = self.station_data[station]
        x, y = self.xy2lonlat(stn['Longitude'], stn['Latitude'], inverse=True)
        coord = np.c_[x, y, stn['Elevation']]
        return coord

    def station_offset(self, station=None):
        coord = self.station_xyz(station)
        return coord - self.grid_centre

    @property
    def maps(self):
        """Get and set the traveltime tables"""
        return self._maps

    @maps.setter
    def maps(self, value):
        self._maps = value

    def _select_station(self, station_data):
        if self.station_data is None:
            return station_data

        nstn = len(self.station_data)
        flag = np.array(np.zeros(nstn, dtype=np.bool))
        for i, stn in enumerate(self.station_data['Name']):
            if stn in station_data:
                flag[i] = True

    def decimate(self, ds, inplace=False):
        """
        Up- or down-sample the travel-time tables by some factor

        Parameters
        ----------
        ds : 

        inplace : bool
            Performs the operation to the travel-time table directly

        TO-DO
        -----
        I'm not sure that the inplace operation is doing the right thing? - CB


        """

        if not inplace:
            self = copy(self)
            self.maps = copy(self.maps)
        else:
            self = self

        ds = np.array(ds, dtype=np.int)
        cell_count = 1 + (self.cell_count - 1) // ds
        c1 = (self.cell_count - ds * (cell_count - 1) - 1) // 2
        cn = c1 + ds * (cell_count - 1) + 1
        centre_cell = (c1 + cn - 1) / 2
        centre = self.xyz2loc(centre_cell, inverse=True)
        self.cell_count = cell_count
        self.cell_size = self.cell_size * ds
        self.centre = centre

        maps = self.maps
        if maps is not None:
            for id_, map_ in maps.items():
                maps[id_] = np.ascontiguousarray(map_[c1[0]::ds[0],
                                                      c1[1]::ds[1],
                                                      c1[2]::ds[2], :])
        if not inplace:
            return self

    def decimate_array(self, data, ds):
        self = self
        ds = np.array(ds, dtype=np.int)
        cell_count = 1 + (self.cell_count - 1) // ds
        c1 = (self.cell_count - ds * (cell_count - 1) - 1) // 2
        cn = c1 + ds * (cell_count - 1) + 1
        centre_cell = (c1 + cn - 1) / 2
        centre = self.xyz2loc(centre_cell, inverse=True)
        self.cell_count = cell_count
        self.cell_size = self.cell_size * ds
        self.centre = centre

        array = np.ascontiguousarray(data[c1[0]::ds[0],
                                          c1[1]::ds[1],
                                          c1[2]::ds[2]])
        return array

    def get_values_at(self, loc, station=None):
        val = {}
        for map_ in self.maps.keys():
            val[map_] = self.get_value_at(map_, loc, station)
        return val

    def get_value_at(self, map_, loc, station=None):
        return self.interpolate(map_, loc, station)

    def value_at(self, map_, xyz, station=None):
        loc = self.xyz2loc(xyz)
        return self.interpolate(map_, loc, station)

    def values_at(self, xyz, station=None):
        loc = self.xyz2loc(xyz)
        return self.get_values_at(loc, station)

    def interpolator(self, map_, station=None):
        maps = self.fetch_map(map_, station)
        nc = self.cell_count
        cc = (np.arange(nc[0]), np.arange(nc[1]), np.arange(nc[2]))
        return RegularGridInterpolator(cc, maps, bounds_error=False)

    def interpolate(self, map_, loc, station=None):
        interp_fcn = self.interpolator(map_, station)
        return interp_fcn(loc)

    def fetch_map(self, map_, station=None):
        if station is None:
            return self.maps[map_]
        else:
            station = self._select_station(station)
            return self.maps[map_][..., station]

    def fetch_index(self, map_, sampling_rate, station=None):
        maps = self.fetch_map(map_, station)
        return np.rint(sampling_rate * maps).astype(np.int32)

    def compute_homogeneous_vmodel(self, vp, vs):
        """
        Calculate the travel-time tables for each station in a uniform velocity
        model

        Parameters
        ----------
        vp : float
            P-wave velocity (units: km / s)
        vs : float
            S-wave velocity (units: km / s)

        """

        rloc = self.station_xyz()
        nstn = rloc.shape[0]
        gx, gy, gz = self.grid_xyz
        ncell = self.cell_count
        p_map = np.zeros(np.r_[ncell, nstn])
        s_map = np.zeros(np.r_[ncell, nstn])
        for stn in range(nstn):
            dx = gx - float(rloc[stn, 0])
            dy = gy - float(rloc[stn, 1])
            dz = gz - float(rloc[stn, 2])
            dist = np.sqrt(dx**2 + dy**2 + dz**2)
            p_map[..., stn] = (dist / vp)
            s_map[..., stn] = (dist / vs)
        self.maps = {"TIME_P": p_map,
                     "TIME_S": s_map}

    def compute_1d_vmodel(self, path, delimiter=","):
        """
        Calculate the travel-time tables for each station in a velocity model
        that varies with depth

        Parameters
        ----------
        z : array-like
            Depth of each layer in model (units: km)
        vp : array-like
            P-wave velocity for each layer in model (units: km / s)
        vs : array-like
            S-wave velocity for each layer in model (units: km / s)

        """

        import pandas as pd

        vmod = pd.read_csv(path, delimiter=delimiter).as_matrix()
        z, vp, vs = vmod[:, 0], vmod[:, 1] * 1000, vmod[:, 2] * 1000

        rloc = self.station_xyz()
        nstn = rloc.shape[0]
        ix, iy, iz = self.grid_xyz
        p_map = np.zeros(ix.shape + (rloc.shape[0],))
        s_map = np.zeros(ix.shape + (rloc.shape[0],))

        z = np.insert(np.append(z, -np.inf), 0, np.inf)
        vp = np.insert(np.append(vp, vp[-1]), 0, vp[0])
        vs = np.insert(np.append(vs, vs[-1]), 0, vs[0])

        f = interp1d(z, vp)
        gvp = f(iz)
        f = interp1d(z, vs)
        gvs = f(iz)

        for stn in range(nstn):
            msg = "Generating 1D Travel-Time Table - {} of {}"
            msg = msg.format(stn + 1, nstn)
            print(msg)

            p_map[..., stn] = eikonal(ix, iy, iz,
                                      self.cell_size[0],
                                      self.cell_size[1],
                                      self.cell_size[2],
                                      gvp, rloc[stn][np.newaxis, :])
            s_map[..., stn] = eikonal(ix, iy, iz,
                                      self.cell_size[0],
                                      self.cell_size[1],
                                      self.cell_size[2],
                                      gvs, rloc[stn][np.newaxis, :])

        self.maps = {"TIME_P": p_map,
                     "TIME_S": s_map}

    def compute_3d_vmodel(self, path):
        """

        """
        raise NotImplementedError

    def compute_3d_nlloc_vmodel(self, path, regrid=False, decimate=[1, 1, 1]):
        """
        Calculate the travel-time tables for each station in a velocity model
        that varies over all dimensions.

        This velocity model comes from a NonLinLoc velocity model file.

        Parameters
        ----------
        path : str
            Location of .buf and .hdr files

        Raises
        ------
        MemoryError
            If travel-time grids size exceeds available memory

        """

        nstn = len(self.station_data["Name"])
        for st in range(nstn):
            name = self.station_data["Name"][st]
            msg = "Loading P- and S- traveltime maps for {}"
            msg = msg.format(name)
            print(msg)

            # Reading in P-wave
            self.nlloc_load_file('{}.P.{}.time'.format(path, name))
            if not regrid:
                self.nlloc_project_grid()
            else:
                self.nlloc_regrid(decimate)

            if ("p_map" not in locals()) and ("s_map" not in locals()):
                ncell = self.NLLoc_data.shape
                try:
                    p_map = np.zeros(np.r_[ncell, nstn])
                    s_map = np.zeros(np.r_[ncell, nstn])
                except MemoryError:
                    msg = "P- and S-traveltime maps exceed available memory."
                    raise MemoryError(msg)

            p_map[..., st] = self.NLLoc_data

            self.nlloc_load_file('{}.S.{}.time'.format(path, name))
            if not regrid:
                self.nlloc_project_grid()
            else:
                self.nlloc_regrid(decimate)

            s_map[..., st] = self.NLLoc_data

        self.maps = {"TIME_P": p_map,
                     "TIME_S": s_map}

    def save(self, filename):
        """
        Create a pickle file containing the look-up table

        Parameters
        ----------
        filename : str
            Path to location to save pickle file

        """

        file = open(filename, 'wb')
        pickle.dump(self.__dict__, file, 2)
        file.close()

    def load(self, filename):
        """
        Read the contents of a pickle file to __dict__

        Parameters
        ----------
        filename : str
            Path to pickle file to load

        """

        file = open(filename, 'rb')
        tmp_dict = pickle.load(file)
        self.__dict__.update(tmp_dict)

    def plot_station(self):
        """
        Produce a 2D map view of station locations

        """

        plt.scatter(self.station_data["Longitude"],
                    self.station_data["Latitude"])
        plt.show()

    def plot_3d(self, map_, station, output_file=None):
        """
        Creates a 3-dimensional representation of the station locations with
        optional velocity model if specified.

        Parameters
        ----------
        map_ : str
            Specifies which velocity model to plot
        station : str

        output_file : str, optional
            Location to save file to
        """
        raise NotImplementedError