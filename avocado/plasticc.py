"""Utility functions to interact with the PLAsTiCC dataset"""

import numpy as np
from scipy.special import erf

from .dataset import Dataset
from .utils import settings, AvocadoException, logger

from .augment import Augmentor


class PlasticcAugmentor(Augmentor):
    """Implementation of an Augmentor for the PLAsTiCC dataset"""
    def __init__(self):
        super().__init__()

        self._test_dataset = None
        self._photoz_reference = None

        # Load the photo-z model
        self._load_photoz_reference()

    def _load_test_dataset(self):
        """Load the full PLAsTiCC test dataset to use as a reference for
        augmentation.

        The metadata is cached as self._test_dataset. Only the metadata is
        loaded.

        Returns
        =======
        test_dataset : :class:`Dataset`
            The test dataset loaded with metadata only.
        """
        if self._test_dataset is None:
            self._test_dataset = Dataset.load('plasticc_test',
                                              metadata_only=True)

        return self._test_dataset

    def _load_photoz_reference(self):
        """Load the full PLAsTiCC dataset as a reference for photo-z
        estimation.

        This reads the test set and extracts all of the photo-zs and true
        redshifts. The results are cached as self._photoz_reference.

        Returns
        =======
        photoz_reference numpy ndarray
            A Nx3 array with reference photo-zs for each entry with a spec-z in
            the test set. The columns are spec-z, photo-z and photo-z error.
        """
        if self._photoz_reference is None:
            logger.info("Loading photoz reference...")
            test_dataset = self._load_test_dataset()

            cut = test_dataset.metadata['host_specz'] > 0
            cut_metadata = test_dataset.metadata[cut]

            result = np.vstack([cut_metadata['host_specz'],
                                cut_metadata['host_photoz'],
                                cut_metadata['host_photoz_error']]).T

            self._photoz_reference = result

        return self._photoz_reference

    def _simulate_photoz(self, redshift):
        """Simulate the photoz determination for a lightcurve using the test
        set as a reference.

        I apply the observed differences between photo-zs and spec-zs directly
        to the new redshifts. This does not capture all of the intricacies of
        photo-zs, but it does ensure that we cover all of the available
        parameter space with at least some simulations.

        Parameters
        ----------
        redshift : float
            The new true redshift of the object.

        Returns
        -------
        host_photoz : float
            The simulated photoz of the host.

        host_photoz_error : float
            The simulated photoz error of the host.
        """
        photoz_reference = self._load_photoz_reference()

        while True:
            ref_idx = np.random.choice(len(photoz_reference))
            ref_specz, ref_photoz, ref_photoz_err = photoz_reference[ref_idx]

            # Randomly choose the order for the difference. Degeneracies work
            # both ways, so even if we only see specz=0.2 -> photoz=3.0 in the
            # data, the reverse also happens, but we can't get spec-zs at z=3
            # so we don't see this.
            new_diff = (ref_photoz - ref_specz) * np.random.choice([-1, 1])

            # Apply the difference, and make sure that the photoz is > 0.
            new_photoz = redshift + new_diff
            if new_photoz < 0:
                continue

            # Add some noise to the error so that the classifier can't focus in
            # on it.
            new_photoz_err = ref_photoz_err * np.random.normal(1, 0.05)

            break

        return new_photoz, new_photoz_err

    def _augment_redshift(self, reference_object, augmented_metadata):
        """Choose a new redshift and simulate the photometric redshift for an
        augmented object

        Parameters
        ==========
        reference_object : :class:`AstronomicalObject`
            The object to use as a reference for the augmentation.

        augmented_metadata : dict
            The augmented metadata to add the new redshift too. This will be
            updated in place.
        """
        # Choose a new redshift.
        if reference_object.metadata['galactic']:
            # Galactic object, redshift stays the same
            augmented_metadata['redshift'] = 0
            augmented_metadata['host_specz'] = 0
            augmented_metadata['host_photoz'] = 0
            augmented_metadata['host_photoz_error'] = 0

            # Choose a factor (in magnitudes) to change the brightness by
            augmented_metadata['augment_brightness'] = (
                np.random.normal(0.5, 0.5)
            )
        else:
            # Choose a new redshift based on the reference template redshift.
            template_redshift = reference_object.metadata['redshift']

            # First, we limit the redshift range as a multiple of the original
            # redshift. We avoid making templates too much brighter because
            # the lower redshift templates will be left with noise that is
            # unrealistic. We also avoid going to too high of a relative
            # redshift because the templates there will be too faint to be
            # detected and the augmentor will waste a lot of time without being
            # able to actually generate a template.
            min_redshift = 0.95 * template_redshift
            max_redshift = 5 * template_redshift

            # Second, for high-redshift objects, we add a constraint to make
            # sure that we aren't evaluating the template at wavelengths where
            # the GP extrapolation is unreliable.
            max_redshift = np.min(
                [max_redshift, 1.5 * (1 + template_redshift) - 1]
            )

            # Choose new redshift from a log-uniform distribution over the
            # allowable redshift range.
            aug_redshift = np.exp(np.random.uniform(
                np.log(min_redshift), np.log(max_redshift)
            ))

            # Simulate a new photometric redshift
            aug_photoz, aug_photoz_error = self._simulate_photoz(aug_redshift)
            aug_distmod = self.cosmology.distmod(aug_photoz).value

            augmented_metadata['redshift'] = aug_redshift
            augmented_metadata['host_specz'] = aug_redshift
            augmented_metadata['host_photoz'] = aug_photoz
            augmented_metadata['host_photoz_error'] = aug_photoz_error
            augmented_metadata['distmod'] = aug_distmod

    def _augment_metadata(self, reference_object):
        """Generate new metadata for the augmented object.

        This method needs to be implemented in survey-specific subclasses of
        this class. The new redshift, photoz, coordinates, etc. should be
        chosen in this method.

        Parameters
        ==========
        reference_object : :class:`AstronomicalObject`
            The object to use as a reference for the augmentation.

        Returns
        =======
        augmented_metadata : dict
            The augmented metadata
        """
        augmented_metadata = reference_object.metadata.copy()

        # Choose a new redshift.
        self._augment_redshift(reference_object, augmented_metadata)

        # Choose whether the new object will be in the DDF or not.
        if reference_object.metadata['ddf']:
            # Most observations are WFD observations, so generate more of
            # those. Thee DDF and WFD samples are effectively completely
            # different, so this ratio doesn't really matter.
            augmented_metadata['ddf'] = np.random.rand() > 0.8
        else:
            # If the reference wasn't a DDF observation, can't simulate a DDF
            # observation.
            augmented_metadata['ddf'] = False

        # Smear the mwebv value a bit so that it doesn't uniquely identify
        # points. I leave the position on the sky unchanged (ra, dec, etc.).
        # Don't put any of those variables directly into the classifier!
        augmented_metadata['mwebv'] *= np.random.normal(1, 0.1)

        return augmented_metadata

    def _choose_target_observation_count(self, augmented_metadata):
        """Choose the target number of observations for a new augmented light
        curve.

        We use a functional form that roughly maps out the number of
        observations in the PLAsTiCC test dataset for each of the DDF and WFD
        samples.

        Parameters
        ==========
        augmented_metadata : dict
            The augmented metadata

        Returns
        =======
        target_observation_count : int
            The target number of observations in the new light curve.
        """
        if augmented_metadata['ddf']:
            target_observation_count = int(np.random.normal(330, 30))
        else:
            # I estimate the distribution of number of observations in the
            # WFD regions with a mixture of 3 gaussian distributions.
            gauss_choice = np.random.choice(3, p=[0.05, 0.4, 0.55])
            if gauss_choice == 0:
                mu = 95
                sigma = 20
            elif gauss_choice == 1:
                mu = 115
                sigma = 8
            elif gauss_choice == 2:
                mu = 138
                sigma = 8
            target_observation_count = int(
                np.clip(np.random.normal(mu, sigma), 50, None))

        return target_observation_count

    def _simulate_light_curve_uncertainties(self, observations,
                                            augmented_metadata):
        """Simulate the observation-related noise and detections for a light
        curve.

        For the PLAsTiCC dataset, we estimate the measurement uncertainties for
        each band with a lognormal distribution for both the WFD and DDF
        surveys. Those measurement uncertainties are added to the simulated
        observations.

        Parameters
        ==========
        observations : pandas.DataFrame
            The augmented observations that have been sampled from a Gaussian
            Process. These observations have model flux uncertainties listed
            that should be included in the final uncertainties.
        augmented_metadata : dict
            The augmented metadata

        Returns
        =======
        observations : pandas.DataFrame
            The observations with uncertainties added.
        """
        # Make a copy so that we don't modify the original array.
        observations = observations.copy()

        if len(observations) == 0:
            # No data, skip
            return observations

        if augmented_metadata['ddf']:
            band_noises = {
                'lsstu': (0.68, 0.26),
                'lsstg': (0.25, 0.50),
                'lsstr': (0.16, 0.36),
                'lssti': (0.53, 0.27),
                'lsstz': (0.88, 0.22),
                'lssty': (1.76, 0.23),
            }
        else:
            band_noises = {
                'lsstu': (2.34, 0.43),
                'lsstg': (0.94, 0.41),
                'lsstr': (1.30, 0.41),
                'lssti': (1.82, 0.42),
                'lsstz': (2.56, 0.36),
                'lssty': (3.33, 0.37),
            }

        # Calculate the new noise levels using a lognormal distribution for
        # each band.
        lognormal_parameters = np.array([band_noises[i] for i in
                                         observations['band']])
        add_stds = np.random.lognormal(lognormal_parameters[:, 0],
                                       lognormal_parameters[:, 1])

        noise_add = np.random.normal(loc=0.0, scale=add_stds)
        observations['flux'] += noise_add
        observations['flux_error'] = np.sqrt(
            observations['flux_error']**2 + add_stds**2
        )

        return observations

    def _simulate_detection(self, observations, augmented_metadata):
        """Simulate the detection process for a light curve.

        We model the PLAsTiCC detection probabilities with an error function.
        I'm not entirely sure why this isn't deterministic. The full light
        curve is considered to be detected if there are at least 2 individual
        detected observations.

        Parameters
        ==========
        observations : pandas.DataFrame
            The augmented observations that have been sampled from a Gaussian
            Process.
        augmented_metadata : dict
            The augmented metadata

        Returns
        =======
        observations : pandas.DataFrame
            The observations with the detected flag set.
        pass_detection : bool
            Whether or not the full light curve passes the detection thresholds
            used for the full sample.
        """
        s2n = np.abs(observations['flux']) / observations['flux_error']
        prob_detected = (erf((s2n - 5.5) / 2) + 1) / 2.
        observations['detected'] = np.random.rand(len(s2n)) < prob_detected

        pass_detection = np.sum(observations['detected']) >= 2

        return observations, pass_detection
