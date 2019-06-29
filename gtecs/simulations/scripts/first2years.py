#!/usr/bin/env python
"""Download the LIGO/Virgo First Two Years simulated skymaps, and regrade for use."""

import os
import sys
from subprocess import run

from astropy.table import Table

from gototile.skymap import SkyMap

from tqdm import tqdm as loadingbar


YEARS = ['2015', '2016']


def download():
    """Download the First Two Years data files."""
    # Download the data using wget
    for year in YEARS:
        url = 'https://dcc.ligo.org/LIGO-P1300187/public/'

        # Download the skymap archives and uncompress them
        file = '{}_fits.tar'.format(year)
        run(['wget', url + file])
        run(['tar', '-xf', file])

        # Download the source table
        file = '{}_inj.txt'.format(year)
        run(['wget', url + file])


def reformat():
    """Reformat and combine the source tables."""
    out_file = 'inj.data'
    with open(out_file, 'w') as out:
        # Write columns
        out.write('event_id,simulation_id,mjd,ra,dec,inclination,polarization,coa_phase,'
                  'distance,mass1,mass2,spin1x,spin1y,spin1z,spin2x,spin2y,spin2z\n')

        for year in YEARS:
            # Open the raw file
            file = '{}_inj.txt'.format(year)
            with open(file, 'r') as f:
                for line in f.readlines()[::-1]:
                    # Exit once we've reached the header
                    if line[0] == '-':
                        break

                    # Write out the data seperated by commas
                    out.write(','.join(line.split()) + '\n')


def process(nside=None):
    """Regrade all skymaps, save source info to header and save to a single directory."""
    # Read in the injection table, only keeping the columns we need
    inj_data = Table.read('inj.data', format='ascii.csv')
    data = inj_data['event_id', 'ra', 'dec', 'distance']
    data.add_index('event_id')

    # Load paths to all the skymaps
    fits_paths = []
    for year in YEARS:
        path = '{}_fits'.format(year)
        for direc in os.listdir(path):
            fits_path = os.path.join(path, direc, 'bayestar.fits.gz')
            fits_paths.append(fits_path)

    print('Found {} skymap files'.format(len(fits_paths)))

    # Create the output path
    if nside:
        out_direc = 'skymaps_{:.0f}'.format(nside)
    else:
        out_direc = 'skymaps'
    if not os.path.isdir(out_direc):
        os.mkdir(out_direc)

    # Loop through each skymap
    for fits_path in loadingbar(fits_paths):
        # Load the skymap
        skymap = SkyMap.from_fits(fits_path)

        # Get the event ID
        event_id = int(skymap.object.split(':')[-1])

        # Get the source location and distance
        source_ra = float(data.loc[event_id]['ra'])
        source_dec = float(data.loc[event_id]['dec'])
        source_distance = float(data.loc[event_id]['distance'])

        # Save the properties in the skymap header
        skymap.header['EVENT_ID'] = event_id
        skymap.header['S_RA    '] = source_ra
        skymap.header['S_DEC   '] = source_dec
        skymap.header['S_DIST  '] = source_distance

        # Regrade the skymap to the given nside, if given
        skymap.regrade(nside)

        # Save the skymap to the output directory
        out_file = '{}_bayestar.fits'.format(event_id)
        out_path = os.path.join(out_direc, out_file)
        skymap.save(out_path)

        # Compress the file
        run(['gzip', '-f', out_path])


if __name__ == '__main__':
    if len(sys.argv) > 1:
        nside = int(sys.argv[1])
    else:
        nside = None

    print('Downloading files')
    download()

    print('Reformating source data')
    reformat()

    print('Processing skymaps')
    process(nside)
