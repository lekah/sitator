import numpy as np

from scipy.spatial import ConvexHull
from scipy.spatial.qhull import QhullError

from sitator import SiteNetwork, SiteTrajectory
from sitator.util import PBCCalculator

import logging
logger = logging.getLogger(__name__)

class InsufficientCoordinatingAtomsError(Exception):
    pass

class SiteVolumes(object):
    """Compute the volumes of sites.

    Args:
        error_on_insufficient_coord (bool): To compute an ideal
            site volume (``compute_volumes()``), at least 4 coordinating
            atoms (because we are in 3D space) must be specified in ``vertices``.
            If ``True``, an error will be thrown when a site with less than four
            vertices is encountered; if ``False``, a volume of 0  and surface area
            of NaN will be returned.
    """
    def __init__(self, error_on_insufficient_coord = True):
        self.error_on_insufficient_coord = error_on_insufficient_coord


    def compute_accessable_volumes(self, st, n_recenterings = 8):
        """Computes the volumes of convex hulls around all positions associated with a site.

        Uses the shift-and-wrap trick for dealing with periodicity, so sites that
        take up the majority of the unit cell may give bogus results.

        Adds the ``accessable_site_volumes`` attribute to the ``SiteNetwork``.

        Args:
            st (SiteTrajectory)
            n_recenterings (int): How many different recenterings to try (the
                algorithm will recenter around n of the points and take the minimal
                resulting volume; this deals with cases where there is one outlier
                where recentering around it gives very bad results.)
        """
        assert isinstance(st, SiteTrajectory)
        vols = np.empty(shape = st.site_network.n_sites, dtype = np.float)
        areas = np.empty(shape = st.site_network.n_sites, dtype = np.float)

        pbcc = PBCCalculator(st.site_network.structure.cell)

        for site in range(st.site_network.n_sites):
            pos = st.real_positions_for_site(site)

            assert pos.flags['OWNDATA']

            vol = np.inf
            area = None
            for i in range(n_recenterings):
                # Recenter
                offset = pbcc.cell_centroid - pos[int(i * (len(pos)/n_recenterings))]
                pos += offset
                pbcc.wrap_points(pos)

                try:
                    hull = ConvexHull(pos)
                except QhullError as qhe:
                    logger.warning("For site %i, iter %i: %s" % (site, i, qhe))
                    vols[site] = np.nan
                    areas[site] = np.nan
                    continue

                if hull.volume < vol:
                    vol = hull.volume
                    area = hull.area

            vols[site] = vol
            areas[site] = area

        st.site_network.add_site_attribute('accessable_site_volumes', vols)


    def compute_volumes(self, sn):
        """Computes the volume of the convex hull defined by each sites' static verticies.

        Requires vertex information in the SiteNetwork.

        Adds the ``site_volumes`` and ``site_surface_areas`` attributes.

        Volumes can be NaN for degenerate hulls/point sets on which QHull fails.

        Args:
            - sn (SiteNetwork)
        """
        assert isinstance(sn, SiteNetwork)
        if sn.vertices is None:
            raise ValueError("SiteNetwork must have verticies to compute volumes!")

        vols = np.empty(shape = sn.n_sites, dtype = np.float)
        areas = np.empty(shape = sn.n_sites, dtype = np.float)

        pbcc = PBCCalculator(sn.structure.cell)

        for site in range(sn.n_sites):
            pos = sn.static_structure.positions[list(sn.vertices[site])]
            if len(pos) < 4:
                if self.error_on_insufficient_coord:
                    raise InsufficientCoordinatingAtomsError("Site %i had only %i vertices (less than needed 4)" % (site, len(pos)))
                else:
                    vols[site] = 0
                    areas[site] = np.nan
                    continue

            assert pos.flags['OWNDATA'] # It should since we're indexing with index lists
            # Recenter
            offset = pbcc.cell_centroid - sn.centers[site]
            pos += offset
            pbcc.wrap_points(pos)

            try:
                hull = ConvexHull(pos)
                vols[site] = hull.volume
                areas[site] = hull.area
            except QhullError as qhe:
                logger.warning("Had QHull failure when computing volume of site %i" % site)
                vols[site] = np.nan
                areas[site] = np.nan

        sn.add_site_attribute('site_volumes', vols)
        sn.add_site_attribute('site_surface_areas', areas)


    def run(self, st):
        """For backwards compatability."""
        self.compute_accessable_volumes(st)
