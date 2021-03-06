
import numpy as np
from abc import ABCMeta, abstractmethod

from sitator import SiteNetwork, SiteTrajectory
from sitator.util.progress import tqdm

from ase.data import atomic_numbers

import logging
logger = logging.getLogger(__name__)

class SOAP(object, metaclass=ABCMeta):
    """Abstract base class for computing SOAP vectors in a SiteNetwork.

    SOAP computations are *not* thread-safe; use one SOAP object per thread.

    :param int tracer_atomic_number: The atomic number of the tracer.
    :param list environment: The atomic numbers or atomic symbols
        of the environment to consider. I.e. for Li2CO3, can be set to ['O']  or [8]
        for oxygen only, or ['C', 'O'] / ['C', 8] / [6,8] if carbon and oxygen
        are considered an environment.
        Defaults to ``None``, in which case all non-mobile atoms are considered
        regardless of species.
    :param soap_mask: Which atoms in the ``SiteNetwork``'s structure
        to use in SOAP calculations.
        Can be either a boolean mask ndarray or a tuple of species.
        If ``None``, the entire ``static_structure`` of the ``SiteNetwork`` will be used.
        Mobile atoms cannot be used for the SOAP host structure.
        Even not masked, species not considered in environment will be not accounted for.
        For ideal performance: Specify environment and ``soap_mask`` correctly!
    :param dict soap_params = {}: Any custom SOAP params.
    :param func backend: A function that can be called with
        ``sn, soap_mask, tracer_atomic_number, environment_list`` as
        parameters, returning a function that, given the current soap structure
        along with tracer atoms, returns SOAP vectors in a numpy array. (i.e.
        its signature is ``soap(structure, positions)``). The returned function
        can also have a property, ``n_dim``, giving the length of a single SOAP
        vector.
    """

    from .backend.quip import quip_soap_backend as backend_quip
    from .backend.dscribe import dscribe_soap_backend as backend_dscribe

    def __init__(self, tracer_atomic_number, environment = None,
            soap_mask = None,
            backend = None):
        from ase.data import atomic_numbers

        self.tracer_atomic_number = tracer_atomic_number
        self._soap_mask = soap_mask

        if backend is None:
            backend = SOAP.dscribe_soap_backend
        self._backend = backend

        # - Standardize environment species controls if given
        if not environment is None: # User given environment
            if not isinstance(environment, (list, tuple)):
                raise TypeError('environment has to be a list or tuple of species (atomic number'
                    ' or symbol of the environment to consider')

            environment_list = []
            for e in environment:
                if isinstance(e, int):
                    assert 0 < e <= max(atomic_numbers.values())
                    environment_list.append(e)
                elif isinstance(e, str):
                    try:
                        environment_list.append(atomic_numbers[e])
                    except KeyError:
                        raise KeyError("You provided a string that is not a valid atomic symbol")
                else:
                    raise TypeError("Environment has to be a list of atomic numbers or atomic symbols")

            self._environment = environment_list
        else:
            self._environment = None

    def get_descriptors(self, stn):
        """Get the descriptors.

        Args:
            stn (SiteTrajectory or SiteNetwork)
        Returns:
            An array of descriptor vectors and an equal length array of labels
            indicating which descriptors correspond to which sites.
        """
        # Build SOAP host structure
        if isinstance(stn, SiteTrajectory):
            sn = stn.site_network
        elif isinstance(stn, SiteNetwork):
            sn = stn
        else:
            raise TypeError("`stn` must be SiteNetwork or SiteTrajectory")

        structure, tracer_atomic_number, soap_mask = self._make_structure(sn)

        if self._environment is not None:
            environment_list = self._environment
        else:
            # Set it to all species represented by the soap_mask
            environment_list = np.unique(sn.structure.get_atomic_numbers()[soap_mask])

        soaper = self._backend(sn, soap_mask, tracer_atomic_number, environment_list)

        # Compute descriptors
        return self._get_descriptors(stn, structure, tracer_atomic_number, soap_mask, soaper)

    # ----

    def _make_structure(self, sn):

        if self._soap_mask is None:
            # Make a copy of the static structure
            structure = sn.static_structure.copy()
            soap_mask = sn.static_mask # soap mask is the
        else:
            if isinstance(self._soap_mask, tuple):
                soap_mask = np.in1d(sn.structure.get_chemical_species(), self._soap_mask)
            else:
                soap_mask = self._soap_mask

            assert not np.any(soap_mask & sn.mobile_mask), "Error for atoms %s; No atom can be both static and mobile" % np.where(soap_mask & sn.mobile_mask)[0]
            structure = sn.structure[soap_mask]

        assert np.any(soap_mask), "Given `soap_mask` excluded all host atoms."
        if not self._environment is None:
            assert np.any(np.isin(sn.structure.get_atomic_numbers()[soap_mask], self._environment)), "Combination of given `soap_mask` with the given `environment` excludes all host atoms."

        # Add a tracer
        if self.tracer_atomic_number is None:
            tracer_atomic_number = sn.structure.get_atomic_numbers()[sn.mobile_mask][0]
        else:
            tracer_atomic_number = self.tracer_atomic_number

        if np.any(structure.get_atomic_numbers() == tracer_atomic_number):
            raise ValueError("Structure cannot have static atoms (that are enabled in the SOAP mask) of the same species as `tracer_atomic_number`.")

        structure.set_pbc([True, True, True])

        return structure, tracer_atomic_number, soap_mask


    @abstractmethod
    def _get_descriptors(self, stn, structure, tracer_atomic_number, soaper):
        pass




class SOAPCenters(SOAP):
    """Compute the SOAPs of the site centers in the fixed host structure.

    Requires a ``SiteNetwork`` as input.
    """
    def _get_descriptors(self, sn, structure, tracer_atomic_number, soap_mask, soaper):
        if isinstance(sn, SiteTrajectory):
            sn = sn.site_network
        assert isinstance(sn, SiteNetwork), "SOAPCenters requires a SiteNetwork or SiteTrajectory, not `%s`" % sn

        pts = sn.centers

        out = soaper(structure, pts)

        return out, np.arange(sn.n_sites)


class SOAPSampledCenters(SOAPCenters):
    """Compute the SOAPs of representative points for each site, as determined by ``sampling_transform``.

    Takes either a ``SiteNetwork`` or ``SiteTrajectory`` as input; requires that
    ``sampling_transform`` produce a ``SiteNetwork`` where ``site_types`` indicates
    which site in the original ``SiteNetwork``/``SiteTrajectory`` it was sampled from.

    Typical sampling transforms are ``sitator.misc.NAvgsPerSite`` (for a ``SiteTrajectory``)
    and ``sitator.misc.GenerateAroundSites`` (for a ``SiteNetwork``).
    """
    def __init__(self, *args, **kwargs):
        self.sampling_transform = kwargs.pop('sampling_transform', 1)
        super(SOAPSampledCenters, self).__init__(*args, **kwargs)

    def get_descriptors(self, stn):
        # Do sampling
        sampled = self.sampling_transform.run(stn)
        assert isinstance(sampled, SiteNetwork), "Sampling transform returned `%s`, not a SiteNetwork" % sampled

        # Compute actual dvecs
        dvecs, _ = super(SOAPSampledCenters, self).get_descriptors(sampled)

        # Return right corersponding sites
        return dvecs, sampled.site_types



class SOAPDescriptorAverages(SOAP):
    """Compute many instantaneous SOAPs for each site, and then average them in SOAP space.

    Computes the SOAP descriptors for mobile particles assigned to each site,
    in the host structure *as it was at that moment*. Those descriptor vectors are
    then averaged in SOAP space to give the final SOAP vectors for each site.

    This method often performs better than SOAPSampledCenters on more dynamic
    systems, but requires significantly more computation.

    :param int stepsize: Stride (in frames) when computing SOAPs. Default 1.
    :param int averaging: Number of SOAP vectors to average for each output vector.
    :param int avg_descriptors_per_site: Can be specified instead of ``averaging``.
        Specifies the _average_ number of average SOAP vectors to compute for each
        site. This does not guerantee that number of SOAP vectors for any site,
        rather, it allows a trajectory-size agnostic way to specify approximately
        how many descriptors are desired.

    """
    def __init__(self, *args, **kwargs):

        averaging_key = 'averaging'
        stepsize_key = 'stepsize'
        avg_desc_per_key = 'avg_descriptors_per_site'

        assert not ((averaging_key in kwargs) and (avg_desc_per_key in kwargs)), "`averaging` and `avg_descriptors_per_site` cannot be specified at the same time."

        self._stepsize = kwargs.pop(stepsize_key, 1)

        d = {stepsize_key : self._stepsize}

        if averaging_key in kwargs:
            self._averaging = kwargs.pop(averaging_key)
            d[averaging_key] = self._averaging
            self._avg_desc_per_site = None
        elif avg_desc_per_key in kwargs:
            self._avg_desc_per_site = kwargs.pop(avg_desc_per_key)
            d[avg_desc_per_key] = self._avg_desc_per_site
            self._averaging = None
        else:
            raise RuntimeError("Either the `averaging` or `avg_descriptors_per_site` option must be provided.")

        for k,v in d.items():
            if not isinstance(v, int):
                raise TypeError('{} has to be an integer'.format(k))
            if not ( v > 0):
                raise ValueError('{} has to be an positive'.format(k))
        del d # not needed anymore!

        super(SOAPDescriptorAverages, self).__init__(*args, **kwargs)


    def _get_descriptors(self, site_trajectory, structure, tracer_atomic_number, soap_mask, soaper):
        """
        calculate descriptors
        """
        # the number of sites in the network
        nsit = site_trajectory.site_network.n_sites
        # I load the indices of the mobiles species into mob_indices:
        mob_indices = np.where(site_trajectory.site_network.mobile_mask)[0]
        # real_traj is the real space positions, site_traj the site trajectory
        # (i.e. for every mobile species the site index)
        real_traj = site_trajectory._real_traj[::self._stepsize]
        site_traj = site_trajectory.traj[::self._stepsize]

        # Now, I need to allocate the output
        # so for each site, I count how much data there is!
        counts = np.zeros(shape = nsit + 1, dtype = np.int)
        for frame in site_traj:
            counts[frame] += 1 # A duplicate in `frame` will still only cause a single addition due to Python rules.
        counts = counts[:-1]

        if self._averaging is not None:
            averaging = self._averaging
        else:
            averaging = int(np.floor(np.mean(counts) / self._avg_desc_per_site))
        logger.debug("Will average %i SOAP vectors for every output vector" % averaging)

        if averaging == 0:
            logger.warning("Asking for too many average descriptors per site; got averaging = 0; setting averaging = 1")
            averaging = 1

        nr_of_descs = counts // averaging
        insufficient = nr_of_descs == 0
        if np.any(insufficient):
            logger.warning("You're asking to average %i SOAP vectors, but at this stepsize, %i sites are insufficiently occupied. Num occ./averaging: %s" % (averaging, np.sum(insufficient), counts[insufficient] / averaging))
        averagings = np.full(shape = len(nr_of_descs), fill_value = averaging)
        averagings[insufficient] = counts[insufficient]
        nr_of_descs = np.maximum(nr_of_descs, 1) # If it's 0, just make one with whatever we've got
        assert np.all(nr_of_descs >= 1)
        logger.debug("Minimum # of descriptors/site: %i; maximum: %i" % (np.min(nr_of_descs), np.max(nr_of_descs)))
        # This is where I load the descriptor:
        descs = np.zeros(shape = (np.sum(nr_of_descs), soaper.n_dim))

        # An array that tells  me the index I'm at for each site type
        desc_index = np.asarray([np.sum(nr_of_descs[:i]) for i in range(len(nr_of_descs))])
        max_index = np.asarray([np.sum(nr_of_descs[:i+1]) for i in range(len(nr_of_descs))])

        count_of_site = np.zeros(len(nr_of_descs), dtype=int)
        allowed = np.ones(nsit, dtype = np.bool)

        for site_traj_t, pos in zip(tqdm(site_traj, desc="SOAP Frame"), real_traj):
            # I update the host lattice positions here, once for every timestep
            structure.positions[:] = pos[soap_mask]

            to_describe = (site_traj_t != SiteTrajectory.SITE_UNKNOWN) & allowed[site_traj_t]

            if np.any(to_describe):
                sites_to_describe = site_traj_t[to_describe]
                soaps = soaper(structure, pos[mob_indices[to_describe]])
                soaps /= averagings[sites_to_describe][:, np.newaxis]
                idx_to_add_desc = desc_index[sites_to_describe]
                descs[idx_to_add_desc] += soaps
                count_of_site[sites_to_describe] += 1

            # Reset and increment full averages
            full_average = count_of_site == averagings
            desc_index[full_average] += 1
            count_of_site[full_average] = 0
            allowed[max_index == desc_index] = False

        assert not np.any(allowed) # We should have maxed out all of them after processing all frames.

        desc_to_site = np.repeat(list(range(nsit)), nr_of_descs)
        return descs, desc_to_site
