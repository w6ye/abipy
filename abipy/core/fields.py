# coding: utf-8
"""This module contains the class describing densities in real space on uniform 3D meshes."""
from __future__ import print_function, division, unicode_literals, absolute_import

import numpy as np
import collections

from monty.collections import AttrDict
from monty.functools import lazy_property
from monty.string import is_string
from pymatgen.core.units import bohr_to_angstrom
from pymatgen.io.vasp.inputs import Poscar
from pymatgen.io.vasp.outputs import Chgcar
from abipy.core.structure import Structure
from abipy.core.mesh3d import Mesh3D
from abipy.core.func1d import Function1D
from abipy.core.mixins import Has_Structure
from abipy.tools import transpose_last3dims
from abipy.iotools import Visualizer, xsf, ETSF_Reader, cube

__all__ = [
    "ScalarField",
    "Density",
    #"Potential",
]


class ScalarField(Has_Structure):
    """
    Base class representing a set of spin-dependent scalar fields generated by electrons (e.g. densities, potentials).
    Data is represented on a homogenous real-space mesh.
    A `ScalarField` has a structure object and provides helper functions to perform common operations
    such computing integrals, FFT transforms ...
    """

    def __init__(self, nspinor, nsppol, nspden, datar, structure, iorder="c"):
        """
        Args:
            nspinor: Number of spinorial components.
            nsppol: Number of spins.
            nspden: Number of spin density components.
            datar: numpy array with the scalar field in real space. shape [..., nx, ny, nz]
            structure: :class:`Structure` object describing the crystalline structure.
            iorder: Order of the array. "c" for C ordering, "f" for Fortran ordering.
        """
        self.nspinor, self.nsppol, self.nspden = nspinor, nsppol, nspden
        # Convert to Abipy Structure.
        self._structure = Structure.as_structure(structure)

        iorder = iorder.lower()
        assert iorder in ["f", "c"]

        if iorder == "f":
            # (z,x,y) --> (x,y,z)
            datar = transpose_last3dims(datar)

        # Init Mesh3D
        mesh_shape = datar.shape[-3:]
        self._mesh = Mesh3D(mesh_shape, structure.lattice.matrix)

        # Make sure we have the correct shape.
        self._datar = np.reshape(datar, (nspden,) + self.mesh.shape)

    def __len__(self):
        return len(self.datar)

    def __str__(self):
        return self.to_string()

    def _check_other(self, other):
        """Consistency check"""
        if not isinstance(other, self.__class__):
            raise TypeError('object of class %s is not an instance of %s' % (other.__class__, self.__class__))

        if any([self.nspinor != other.nspinor, self.nsppol != other.nsppol, self.nspden != other.nspden,
                self.structure != other.structure, self.mesh != other.mesh]):
            raise ValueError('Incompatible scalar fields')

        return True

    def __add__(self, other):
        """self + other"""
        self._check_other(other)
        return self.__class__(nspinor=self.nspinor, nsppol=self.nsppol, nspden=self.nspden,
                              datar=self.datar + other.datar,
                              structure=self.structure, iorder="c")

    def __sub__(self, other):
        """self - other"""
        self._check_other(other)
        return self.__class__(nspinor=self.nspinor, nsppol=self.nsppol, nspden=self.nspden,
                              datar=self.datar - other.datar,
                              structure=self.structure, iorder="c")

    def __neg__(self):
        """-self"""
        return self.__class__(nspinor=self.nspinor, nsppol=self.nsppol, nspden=self.nspden,
                              datar=-self.datar,
                              structure=self.structure, iorder="c")

    @property
    def structure(self):
        """Structure object."""
        return self._structure

    def to_string(self, prtvol=0):
        """String representation"""
        lines = ["%s: nspinor = %i, nsppol = %i, nspden = %i" %
                 (self.__class__.__name__, self.nspinor, self.nsppol, self.nspden)]
        app = lines.append
        app(self.mesh.to_string(prtvol))
        if prtvol > 0: app(str(self.structure))

        return "\n".join(lines)

    @property
    def datar(self):
        """`ndarray` with data in real space. shape: [nspden, nx, ny, nz]"""
        return self._datar

    @lazy_property
    def datag(self):
        """`ndarrray` with data in reciprocal space. shape: [nspden, nx, ny, nz]"""
        # FFT R --> G.
        return self.mesh.fft_r2g(self.datar)

    @property
    def mesh(self):
        """:class:`Mesh3D`. datar and datag are defined on this mesh."""
        return self._mesh

    @property
    def shape(self):
        """Shape of the array."""
        assert self.datar.shape == self.datag.shape
        return self.datar.shape

    @property
    def nx(self):
        """Number of points along x."""
        return self.mesh.nx

    @property
    def ny(self):
        """Number of points along y."""
        return self.mesh.ny

    @property
    def nz(self):
        """Number of points along z."""
        return self.mesh.nz

    @property
    def is_collinear(self):
        """True if collinear i.e. nspinor==1."""
        return self.nspinor == 1

    #@property
    #def datar_xyz(self):
    #    """
    #    Returns a copy of the real space data with shape [:, nx, ny, nz].
    #    Mainly used for post-processing.
    #    """
    #    return self.mesh.reshape(self.datar).copy()

    #@property
    #def datag_xyz(self):
    #    """
    #    Returns a copy of the reciprocal space data with shape [:, nx, ny, nz].
    #    Mainly used for post-processing.
    #    """
    #    return self.mesh.reshape(self.datag).copy()

    @staticmethod
    def _check_space(space):
        """Helper function used in __add__ ... methods to check Consistency."""
        space = space.lower()
        if space not in ("r", "g"):
            raise ValueError("Wrong space %s" % space)
        return space

    def mean(self, space="r", axis=0):
        """
        Returns the average of the array elements along the given axis.
        """
        if "r" == self._check_space(space):
            return self.datar.mean(axis=axis)
        else:
            return self.datag.mean(axis=axis)

    def std(self, space="r", axis=0):
        """
        Returns the standard deviation of the array elements along the given axis.
        """
        if "r" == self._check_space(space):
            return self.datar.std(axis=axis)
        else:
            return self.datag.std(axis=axis)

    #def spheres_indexarr(self, symbrad=None):
    #    if not hasattr(self, "_cached_spheres_indexarr"):
    #        self._cached_spheres_indexarr = collections.deque(5)
    #    for d, arr in self._cached_spheres_indexarr:
    #        if d == symbrad: return arr
    #    indarr = self.calc_ind_arr(symbrad)
    #    self._cached_spheres_indexarr.append(symbrad, indarr)
    #    return iarr

    #def braket_waves(self, bra_wave, ket_wave):
    #    """
    #    Compute the matrix element of <bra_wave|datar|ket_wave> in real space
    #    """

    #    if bra_wave.mesh != self.mesh:
    #       bra_ur = bra_wave.fft_ug(self.mesh)
    #    else:
    #       bra_ur = bra_wave.ur

    #    if ket_wave.mesh != self.mesh:
    #       ket_ur = ket_wave.fft_ug(self.mesh)
    #    else:
    #       ket_ur = ket_wave.ur

    #    if self.nspinor == 1:
    #        assert bra_wave.spin == ket_wave.spin
    #       datar_spin = self.datar[bra_wave.spin]
    #       return self.mesh.integrate(bra_ur.conj() * datar_spin * ket_ur)
    #    else:
    #        raise NotImplemented("nspinor != 1 not implmenented")

    #def map_coordinates(self, rcoords, order=3, frac_coords=True)
    #    """
    #    Interpolate the real space data
    #
    #    Args:
    #        coordinates: array_like
    #           The coordinates at which input is evaluated.
    #        order: int, optional
    #            The order of the spline interpolation, default is 3. The order has to be in the range 0-5.
    #    Returns:
    #       ndarray with the interpolated results.
    #    """
    #    from scipy.ndimage.interpolation import map_coordinates
    #    # Compute the fractional coordinates at which datar is interpolated.
    #    rcoords = np.asarray(rcoords)
    #    if not frac_coords:
    #       rcoords = self.structure.to_frac_coords(rcoords, in_cell=True)
    #    # Wrap in the unit cell.
    #    rcoords %= 1
    #    coordinates = [rcoords[0], rcoords[1], rcoords[2]]

    #    Interpolate the real part.
    #    interp_data = []
    #    for indata in self.datar_xyz:
    #        assert not np.iscomple(indata)
    #        interp_data.append(map_coordinates(indata.real, coordinates, order=order))

    #    return np.array(interp_data)

    #def fourier_interp(self, new_mesh):
        #intp_datar = self.mesh.fourier_interp(self.datar, new_mesh, inspace="r")
        #return self.__class__(self.nspinor, self.nsppol, self.nspden, self.structure, intp_datar)

    def export(self, filename, visu=None):
        """
        Export the real space data to file filename.

        Args:
            filename: String specifying the file path and the file format.
                The format is defined by the file extension. filename="prefix.xsf", for example,
                will produce a file in XSF format. An *empty* prefix, e.g. ".xsf" makes the code use a temporary file.
            visu:
               :class:`Visualizer` subclass. By default, this method returns the first available
                visualizer that supports the given file format. If visu is not None, an
                instance of visu is returned. See :class:`Visualizer` for the list of
                applications and formats supported.

        Returns:
            Instance of :class:`Visualizer`
        """
        if "." not in filename:
            raise ValueError("Cannot detect file extension in filename: %s " % filename)

        tokens = filename.strip().split(".")
        ext = tokens[-1]

        if not tokens[0]: # filename == ".ext" ==> Create temporary file.
            import tempfile
            filename = tempfile.mkstemp(suffix="." + ext, text=True)[1]

        with open(filename, mode="wt") as fh:
            if ext == "xsf":
                # xcrysden
                xsf.xsf_write_structure(fh, self.structure)
                xsf.xsf_write_data(fh, self.structure, self.datar, add_replicas=True)
            else:
                raise NotImplementedError("extension %s is not supported." % ext)

        if visu is None:
            return Visualizer.from_file(filename)
        else:
            return visu(filename)

    def visualize(self, visu_name):
        """
        Visualize data with visualizer.

        See :class:`Visualizer` for the list of applications and formats supported.
        """
        visu = Visualizer.from_name(visu_name)

        # Try to export data to one of the formats supported by the visualizer
        # Use a temporary file (note "." + ext)
        for ext in visu.supported_extensions():
            ext = "." + ext
            try:
                return self.export(ext, visu=visu)
            except visu.Error:
                pass
        else:
            raise visu.Error("Don't know how to export data for visualizer %s" % visu_name)

    #def get_line(self, line, space="r"):
    #    x, y, z = self.mesh.line_inds(line)
    #    space = self._check_space(space)
    #    if space == "r":
    #       line = self.datar_xyz[:, x, y, z]
    #    elif space == "g":
    #       line = self.datag_xyz[:, x, y, z]
    #    # Return a 2D array.
    #    new_shape = lines.shape[0] + tuple(s for s in shape[-3:] is s)
    #    return np.reshape(line, new_shape)

    #def get_plane(self, plane, h, space="r"):
    #    x, y, z = self.mesh.plane_inds(plane, h=h)
    #    space = self._check_space(space)
    #    if space == "r":
    #       plane = self.datar_xyz[:, x, y, z]
    #    elif space == "g":
    #       plane = self.datag_xyz[:, x, y, z]
    #    # Return a 3D array.
    #    new_shape = lines.shape[0] + tuple(s for s in shape[-3:] is s)
    #    return np.reshape(plane, new_shape)


class Density(ScalarField):
    """
    Electronic density.

    .. note::

        Unlike in the Abinit code, datar[nspden] contains the up/down components if nsppol = 2
    """

    @classmethod
    def from_file(cls, filepath):
        """Initialize the object from a netCDF file."""
        with DensityReader(filepath) as r:
            return r.read_density(cls=cls)

    @classmethod
    def ae_core_density_on_mesh(cls, valence_density, structure, rhoc_files, maxr=2.0, nelec=None,
                                method='mesh3d_dist_gridpoints', small_dist_mesh=(8, 8, 8), small_dist_factor=1.5):
        """
        Initialize the all electron core density of the structure from the pseudopotentials *rhoc* files
        Note that these *rhoc* files contain one column with the radii in Bohrs and one column with the density
        in #/Bohr^3 multiplied by a factor 4pi.
        """
        rhoc_atom_splines = [None]*len(structure)
        if isinstance(rhoc_files, (list, tuple)):
            if len(structure) != len(rhoc_files):
                raise ValueError('Number of rhoc_files should be equal to the number of sites in the structure')
            for ifname, fname in rhoc_files:
                rad_rho = np.fromfile(fname, sep=' ')
                rad_rho = rad_rho.reshape((len(rad_rho)/2, 2))
                radii = rad_rho[:, 0] * bohr_to_angstrom
                rho = rad_rho[:, 1] / (4.0*np.pi) / (bohr_to_angstrom ** 3)
                func1d = Function1D(radii, rho)
                rhoc_atom_splines[ifname] = func1d.spline

        elif isinstance(rhoc_files, collections.Mapping):
            atoms_symbols = [elmt.symbol for elmt in structure.composition]
            if not np.all([atom in rhoc_files for atom in atoms_symbols]):
                raise ValueError('The rhoc_files should be provided for all the atoms in the structure')
            splines = {}
            for symbol, fname in rhoc_files.items():
                rad_rho = np.fromfile(fname, sep=' ')
                rad_rho = rad_rho.reshape((len(rad_rho)/2, 2))
                radii = rad_rho[:, 0] * bohr_to_angstrom
                rho = rad_rho[:, 1] / (4.0*np.pi) / (bohr_to_angstrom ** 3)
                func1d = Function1D(radii, rho)
                splines[symbol] = func1d.spline
            for isite, site in enumerate(structure):
                rhoc_atom_splines[isite] = splines[site.specie.symbol]

        core_den = np.zeros_like(valence_density.datar)
        dvx = valence_density.mesh.dvx
        dvy = valence_density.mesh.dvy
        dvz = valence_density.mesh.dvz
        maxdiag = max([np.linalg.norm(dvx+dvy+dvz),
                       np.linalg.norm(dvx+dvy-dvz),
                       np.linalg.norm(dvx-dvy+dvz),
                       np.linalg.norm(dvx-dvy-dvz)])
        smallradius = small_dist_factor*maxdiag

        if method == 'get_sites_in_sphere':
            for ix in range(valence_density.mesh.nx):
                for iy in range(valence_density.mesh.ny):
                    for iz in range(valence_density.mesh.nz):
                        rpoint = valence_density.mesh.rpoint(ix=ix, iy=iy, iz=iz)
                        # TODO: optimize this !
                        sites = structure.get_sites_in_sphere(pt=rpoint, r=maxr, include_index=True)
                        for site, dist, site_index in sites:
                            if dist > smallradius:
                                core_den[0, ix, iy, iz] += rhoc_atom_splines[site_index](dist)
                            # For small distances, integrate over the small volume dv around the point as the core
                            # density is extremely high close to the atom
                            else:
                                total = 0.0
                                nnx, nny, nnz = small_dist_mesh
                                ddvx = dvx/nnx
                                ddvy = dvy/nny
                                ddvz = dvz/nnz
                                rpi = rpoint - 0.5 * (dvx + dvy + dvz) + 0.5*ddvx + 0.5*ddvy + 0.5*ddvz
                                for iix in range(nnx):
                                    for iiy in range(nny):
                                        for iiz in range(nnz):
                                            rpoint2 = rpi + iix*ddvx + iiy*ddvy + iiz*ddvz
                                            dist2 = np.linalg.norm(rpoint2 - site.coords)
                                            total += rhoc_atom_splines[site_index](dist2)
                                total /= (nnx*nny*nnz)
                                core_den[0, ix, iy, iz] += total

        elif method == 'mesh3d_dist_gridpoints':
            site_coords = [site.coords for site in structure]
            dist_gridpoints_sites = valence_density.mesh.dist_gridpoints_in_spheres(points=site_coords, radius=maxr)
            for isite, dist_gridpoints_site in enumerate(dist_gridpoints_sites):
                for igp_uc, dist, igp in dist_gridpoints_site:
                    if dist > smallradius:
                        core_den[0, igp_uc[0], igp_uc[1], igp_uc[2]] += rhoc_atom_splines[isite](dist)
                    # For small distances, integrate over the small volume dv around the point as the core density
                    # is extremely high close to the atom
                    else:
                        total = 0.0
                        nnx, nny, nnz = small_dist_mesh
                        ddvx = dvx/nnx
                        ddvy = dvy/nny
                        ddvz = dvz/nnz
                        rpoint = valence_density.mesh.rpoint(ix=igp[0], iy=igp[1], iz=igp[2])
                        rpi = rpoint - 0.5 * (dvx + dvy + dvz) + 0.5*ddvx + 0.5*ddvy + 0.5*ddvz
                        for iix in range(nnx):
                            for iiy in range(nny):
                                for iiz in range(nnz):
                                    rpoint2 = rpi + iix*ddvx + iiy*ddvy + iiz*ddvz
                                    dist2 = np.linalg.norm(rpoint2 - site_coords[isite])
                                    total += rhoc_atom_splines[isite](dist2)
                        total /= (nnx*nny*nnz)
                        core_den[0, igp_uc[0], igp_uc[1], igp_uc[2]] += total
        else:
            raise ValueError('Method "{}" is not allowed'.format(method))

        if nelec is not None:
            sum_elec = np.sum(core_den) * valence_density.mesh.dv
            if np.abs(sum_elec-nelec) / nelec > 0.01:
                raise ValueError('Summed electrons is different from the actual number of electrons by '
                                 'more than 1% ...')
            core_den = core_den / sum_elec * nelec

        return cls(nspinor=1, nsppol=1, nspden=1, datar=core_den, structure=structure, iorder='c')

    def __init__(self, nspinor, nsppol, nspden, datar, structure, iorder="c"):
        """
        Args:
            nspinor: Number of spinorial components.
            nsppol: Number of spins.
            nspden: Number of spin density components.
            datar: `numpy` array with the field in real space.
            structure: structure object.
            iorder: Order of the array. "c" for C ordering, "f" for Fortran ordering.
        """
        super(Density, self).__init__(nspinor, nsppol, nspden, datar, structure, iorder=iorder)

    def get_nelect(self, spin=None):
        """
        Returns the number of electrons with given spin.

        If spin is None, the total number of electrons is computed.
        """
        if self.is_collinear:
            nelect = self.mesh.integrate(self.datar)
            return np.sum(nelect) if spin is None else nelect[spin]
        else:
            return self.mesh.integrate(self.datar[0])

    @lazy_property
    def total_rhor(self):
        """
        numpy array with the total density in real space on the FFT mesh
        """
        if self.is_collinear:
            if self.nsppol == 1:
                if self.nspden == 2: raise NotImplementedError()
                return self.datar[0]
            elif self.nsppol == 2:
                #tot_rhor = np.sum(self.datar, axis=0)
                return self.datar[0] + self.datar[1]
            else:
                raise ValueError("You should not be here")

        # Non collinear case.
        raise NotImplementedError

    def total_rhor_as_density(self):
        """Return a `Density` object with the total density."""
        return self.__class__(nspinor=1, nsppol=1, nspden=1, datar=self.total_rhor,
                              structure=self.structure, iorder="c")

    @lazy_property
    def total_rhog(self):
        """numpy array with the total density in G-space."""
        # FFT R --> G.
        return self.mesh.fft_r2g(self.total_rhor)

    @lazy_property
    def magnetization_field(self):
        """
        numpy array with the magnetization field in real space on the FFT mesh:

            #. 0 if spin-unpolarized calculation
            #. spin_up - spin_down if collinear spin-polarized
            #. numpy array with (mx, my, mz) components if non-collinear magnetism
        """
        if self.is_collinear:
            if self.nsppol == 1 and self.nspden == 1:
                # zero magnetization by definition.
                return self.mesh.zeros()
            else:
                # spin_up - spin_down.
                return self.datar[0] - self.datar[1]
        else:
            # mx, my, mz
            return self.datar[1:]

    @lazy_property
    def magnetization(self):
        """
        Magnetization field integrated over the unit cell.
        Scalar if collinear, vector with mx, my, mz components if non-collinear.
        """
        return self.mesh.integrate(self.magnetization_field)

    @lazy_property
    def nelect_updown(self):
        """
        Tuple with the number of electrons in the up/down channel.
        Return (None, None) if non-collinear.
        """
        if not self.is_collinear:
            return None, None

        if self.nsppol == 1:
            if self.nspden == 2: raise NotImplementedError()
            nup = ndown = self.mesh.integrate(self.datar[0]/2)
        else:
            nup = self.mesh.integrate(self.datar[0])
            ndown = self.mesh.integrate(self.datar[1])

        return nup, ndown

    @lazy_property
    def zeta(self):
        """
        numpy array with Magnetization(r) / total_density(r)
        """
        fact = np.where(self.total_rhor > 1e-16, 1 / self.total_rhor, 0.0)
        return self.magnetization * fact

    #def vhartree(self):
    #    """
    #    Solve the Poisson's equation in reciprocal space.

    #    returns:
    #        (vhr, vhg) Hartree potential in real, reciprocal space.
    #    """
    #    # Compute |G| for each G in the mesh and treat G=0.
    #    gvecs = self.mesh.gvecs
    #    gwork = self.mesh.zeros().ravel()
    #    gnorm = self.structure.gnorm(gvec)

    #    for idx, gg in enumerate(gvecs):
    #        #gnorm = self.structure.gnorm(gg)
    #        gnorm = 1.0  # self.structure.gnorm(gg)

    #        #gg = np.atleast_2d(gg)
    #        #mv = np.dot(self.structure.gmet, gg.T)
    #        #norm2 = 2*np.pi * np.dot(gg, mv)
    #        #gnorm = np.sqrt(norm2)

    #        #print gg, gnorm
    #        if idx != 0:
    #            gwork[idx] = 4*np.pi/gnorm
    #        else:
    #            gwork[idx] = 0.0

    #    new_shape = self.mesh.ndivs
    #    gwork = np.reshape(gwork, new_shape)
    #    #gwork = self.mesh.reshape(gwork)

    #    # FFT to obtain vh in real space.
    #    vhg = self.total_rhog * gwork
    #    vhr = self.mesh.fft_g2r(vhg, fg_ishifted=False)

    #    return vhr, vhg

    def export_to_cube(self, filename, spin='total'):
        """
        Export real space density to CUBE file `filename`.
        """
        if spin != 'total':
            raise ValueError('Argument "spin" should be "total"')

        with open(filename, mode="wt") as fh:
            cube.cube_write_structure_mesh(file=fh, structure=self.structure, mesh=self.mesh)
            cube.cube_write_data(file=fh, data=self.total_rhor, mesh=self.mesh)

    @classmethod
    def from_cube(cls, filename, spin='total'):
        """
        Read real space density to CUBE file `filename`. Return new `Density` instance.
        """
        if spin != 'total':
            raise ValueError('Argument "spin" should be "total"')

        structure, mesh, datar = cube.cube_read_structure_mesh_data(file=filename)
        return cls(nspinor=1, nsppol=1, nspden=1, datar=datar, structure=structure, iorder="c")

    #@lazy_property
    #def kinden(self):
        #"""Compute the kinetic energy density in real- and reciprocal-space."""
        #return kindr, kindgg

    #def vxc(self, xc=None):
        #"""Compute the exchange-correlation potential in real- and reciprocal-space."""
        #return vxcr, vxcg

    def to_chgcar(self, filename=None):
        """
        Convert a `Density` object into a `Chgar` object.
        If `filename` is not None, density is written to this file in `Chgar` format

        Return:
            :class:`Chgcar` instance.

        .. note::

            From: http://cms.mpi.univie.ac.at/vasp/vasp/CHGCAR_file.html:

            This file contains the total charge density multiplied by the volume
            For spinpolarized calculations, two sets of data can be found in the CHGCAR file.
            The first set contains the total charge density (spin up plus spin down),
            the second one the magnetization density (spin up minus spin down).
            For non collinear calculations the CHGCAR file contains the total charge density
            and the magnetisation density in the x, y and z direction in this order.
        """
        myrhor = self.datar * self.structure.volume

        if self.nspinor == 1:
            if self.nsppol == 1:
                data_dict = {"total": myrhor[0]}

            if self.nsppol == 2:
                data_dict = {"total": myrhor[0] + myrhor[1], "diff": myrhor[0] - myrhor[1]}

        elif self.nspinor == 2:
            raise NotImplementedError("pymatgen Chgcar does not implement nspinor == 2")

        chgcar = Chgcar(Poscar(self.structure), data_dict)
        if filename is not None:
            chgcar.write_file(filename)

        return chgcar

    @classmethod
    def from_chgcar_poscar(cls, chgcar, poscar):
        """
        Build a `Density` object from Vasp data.

        Args:
            chgcar: Either string with the name of a CHGCAR file or :class:`Chgcar` pymatgen object.
            poscar: Either string with the name of a POSCAR file or :class:`Poscar` pymatgen object.

        .. warning:

            The present version does not support non-collinear calculations.
            The Chgcar object provided by pymatgen does not provided enough information
            to understand if the calculation is collinear or no.
        """
        if is_string(chgcar):
            chgcar = Chgcar.from_file(chgcar)
        if is_string(poscar):
            poscar = Poscar.from_file(poscar, check_for_POTCAR=False, read_velocities=False)

        nx, ny, nz = chgcar.dim
        nspinor = 1
        nsppol = 2 if chgcar.is_spin_polarized else 1
        nspden = 2 if nsppol == 2 else 1

        # Convert pymatgen chgcar data --> abipy representation.
        abipy_datar = np.empty((nspden, nx, ny, nz))

        if nspinor == 1:
            if nsppol == 1:
                abipy_datar = chgcar.data["total"]
            elif nsppol == 2:
                total, diff = chgcar.data["total"], chgcar.data["diff"]
                abipy_datar[0] = 0.5 * (total + diff)
                abipy_datar[1] = 0.5 * (total - diff)
            else:
                raise ValueError("Wrong nspden %s" % nspden)

        else:
            raise NotImplementedError("nspinor == 2 requires more info in Chgcar")

        # density in Chgcar is multiplied by volume!
        abipy_datar /= poscar.structure.volume

        return cls(nspinor=nspinor, nsppol=nsppol, nspden=nspden, datar=abipy_datar,
                   structure=poscar.structure, iorder="c")


class DensityReader(ETSF_Reader):
    """This object reads density data from a netcdf file."""

    def read_den_dims(self):
        """Returns an :class:`AttrDict` dictionary with the basic dimensions."""
        return AttrDict(
            cplex_den=self.read_dimvalue("real_or_complex_density"),
            nspinor=self.read_dimvalue("number_of_spinor_components"),
            nsppol=self.read_dimvalue("number_of_spins"),
            nspden=self.read_dimvalue("number_of_components"),
            nfft1=self.read_dimvalue("number_of_grid_points_vector1"),
            nfft2=self.read_dimvalue("number_of_grid_points_vector2"),
            nfft3=self.read_dimvalue("number_of_grid_points_vector3"),
        )

    def read_density(self, cls=Density):
        """
        Factory function that builds and returns a `Density` object.
        Note that unlike Abinit, datar[nspden] contains the up/down components if nsppol = 2
        """
        structure = self.read_structure()
        dims = self.read_den_dims()

        # Abinit conventions:
        # rhor(nfft, nspden) = electron density in r space
        # (if spin polarized, array contains total density in first half and spin-up density in second half)
        # (for non-collinear magnetism, first element: total density, 3 next ones: mx,my,mz in units of hbar/2)
        rhor = self.read_value("density")

        if dims.nspden in (1, 4):
            pass
        elif dims.nspden == 2:
            # Store rho_up, rho_down instead of rho_total, rho_up
            total = rhor[0].copy()
            rhor[0] = rhor[1]
            rhor[1] = total - rhor[1]
        else:
            raise RuntimeError("You should not be here")

        # use iorder="f" to transpose the last 3 dimensions since ETSF
        # stores data in Fortran order while abipy uses C-ordering.
        if dims.cplex_den == 1:
            # Get rid of fake last dimensions (cplex).
            rhor = np.reshape(rhor, (dims.nspden, dims.nfft1, dims.nfft2, dims.nfft3))

            # Structure uses Angstrom. Abinit uses bohr.
            rhor /= (bohr_to_angstrom ** 3)
            return cls(dims.nspinor, dims.nsppol, dims.nspden, rhor, structure, iorder="f")

        else:
            raise NotImplementedError("cplex_den %s not coded" % dims.cplex_den)
