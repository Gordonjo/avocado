"""Instrument specific definitions.

This module is used to define properties of various instruments. This should
eventually be split out into some kind of configuration file setup.
"""

band_central_wavelengths = {
    'lsstu': 3671.,
    'lsstg': 4827.,
    'lsstr': 6223.,
    'lssti': 7546.,
    'lsstz': 8691.,
    'lssty': 9710.,
}

# Colors for plotting
band_plot_colors = {
    'lsstu': 'C6',
    'lsstg': 'C4',
    'lsstr': 'C0',
    'lssti': 'C2',
    'lsstz': 'C3',
    'lssty': 'goldenrod',
}
