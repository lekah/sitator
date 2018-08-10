#zeopy: simple Python interface to the Zeo++ `network` tool.
# Alby Musaelian 2018

from __future__ import (absolute_import, division,
                        print_function)

import os
import tempfile
import subprocess
import shutil

import re

import numpy as np

import ase
import ase.io

from sitator.util import PBCCalculator

# TODO: benchmark CUC vs CIF

class Zeopy(object):
    """A wrapper for the Zeo++ `network` tool.

    :warning: Do not use a single instance of Zeopy in parallel.
    """

    def __init__(self, path_to_zeo):
        """Create a Zeopy.

        :param str path_to_zeo: Path to the `network` executable.
        """
        if not (os.path.exists(path_to_zeo) and os.access(path_to_zeo, os.X_OK)):
            raise ValueError("`%s` doesn't seem to be the path to an executable file." % path_to_zeo)
        self._exe = path_to_zeo
        self._tmpdir = None

    def __enter__(self):
        self._tmpdir = tempfile.mkdtemp()

    def __exit__(self, *args):
        shutil.rmtree(self._tmpdir)

    def voronoi(self, structure, radial = False, verbose=True):
        """
        :param Atoms structure: The ASE Atoms to compute the Voronoi decomposition of.
        """

        if self._tmpdir is None:
            raise ValueError("Cannot use Zeopy outside with statement")

        inp = os.path.join(self._tmpdir, "in.cif")
        outp = os.path.join(self._tmpdir, "out.nt2")
        v1out = os.path.join(self._tmpdir, "out.v1")

        ase.io.write(inp, structure)

        # with open(inp, "w") as inf:
        #     inf.write(self.ase2cuc(structure))

        args = []

        if not radial:
            args = ["-nor"]

        try:
            output = subprocess.check_output([self._exe] + args + ["-v1", v1out, "-nt2", outp, inp],
                                             stderr = subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            print("Zeo++ returned an error:", file = sys.stderr)
            print(e.output, file = sys.stderr)
            raise

        if verbose:
            print(output)

        with open(outp, "r") as outf:
            verts, edges = self.parse_nt2(outf.readlines())
        with open(v1out, "r") as outf:
            zeocell = self.parse_v1_cell(outf.readlines())

        # Confirm things really are in order -- sort of
        # Looking at the Zeo code, I don't think it reorders cell vectors --
        # it just rotates them.
        assert np.all(np.linalg.norm(zeocell, axis = 1) - np.linalg.norm(structure.cell, axis = 1) < 0.0001)

        vert_coords = np.asarray([v['coords'] for v in verts])

        zeopbcc = PBCCalculator(zeocell)
        real_pbcc = PBCCalculator(structure.cell)

        # Bring into Zeo crystal coordinates
        zeopbcc.to_cell_coords(vert_coords)
        # Bring into our real coords
        real_pbcc.to_real_coords(vert_coords)

        edges_np = np.empty(shape = (len(edges), 2), dtype = np.int)
        edge_radius = np.empty(shape = len(edges), dtype = np.float)
        for i, edge in enumerate(edges):
            edges_np[i, 0] = edge['from']
            edges_np[i, 1] = edge['to']
            edge_radius[i] = edge['radius']

        return (vert_coords,
               [v['region-atom-indexes'] for v in verts],
               edges_np,
               edge_radius)


    @staticmethod
    def ase2cuc(at):
        """Convert an ase.Atoms to the CUC format.

        See http://www.maciejharanczyk.info/Zeopp/input.html

        :returns: A string in CUC format.
        """
        ls = ["Autogenerated"]
        c = at.get_cell_lengths_and_angles()
        ls.append("Unit_cell: {:0.16f} {:0.16f} {:0.16f} {:0.16f} {:0.16f} {:0.16f}".format(*c))
        for sym, pos in zip(at.get_chemical_symbols(), at.get_scaled_positions()):
            ls.append("{} {:0.16f} {:0.16f} {:0.16f}".format(sym, *pos))

        return "\n".join(ls)

    @staticmethod
    def parse_v1_cell(v1lines):
        # remove blank lines:
        v1lines = iter(filter(None, v1lines))
        # First line is just "Unit cell vectors:"
        assert v1lines.next().strip() == "Unit cell vectors:"
        # Unit cell:
        cell = np.empty(shape = (3, 3), dtype = np.float)
        cellvec_re = re.compile('v[abc]=')
        for i in xrange(3):
            cellvec = v1lines.next().strip().split()
            assert cellvec_re.match(cellvec[0])
            cell[i] = [float(e) for e in cellvec[1:]]
        # number of atoms, etc.
        return cell

    @staticmethod
    def parse_nt2(nt2lines):

        where = None

        vertices = []
        edges = []

        for l in nt2lines:
            if not l.strip():
                continue
            elif l.startswith("Vertex table:"):
                where = 'vertex'
            elif l.startswith("Edge table:"):
                where = 'edge'
            elif where == 'vertex':
                # Line format:
                # [node_number:int] [x] [y] [z] [radius] [region-vertex-atom-indexes]
                e = l.split()
                vertices.append({
                    'number' : int(e[0]),
                    'coords' : np.asarray([float(f) for f in e[1:4]]),
                    'radius' : float(e[4]),
                    'region-atom-indexes' : [int(i) for i in e[5:]]
                })
            elif where == 'edge':
                # Line format:
                # [from node] -> [to node] [radius] [delta uc x] ['' y] ['' z] [length]

                # TODO: For now, just ignore everything but from and to
                e = l.split()
                edges.append({
                    'from' : int(e[0]),
                    'to' : int(e[2]),
                    'radius' : float(e[3])
                })
            else:
                raise RuntimeError("Huh?")

        return vertices, edges
