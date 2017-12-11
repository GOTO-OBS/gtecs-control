"""
Contains some faster coordinate transformations than the ones currently used in astropy.

This is based on an idea put forward by @bwinkel in the pull request located at
at https://github.com/astropy/astropy/pull/6068. This may be merged into the astropy
master at some point. If this happens, this module can be removed.

Simply import into code to experience the speedups; the astropy coordinate transforms are
overwritten on modeul import.
"""

import numpy as np

from astropy.time import Time
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy import _erfa as erfa
from astropy.extern import six
from astropy.coordinates.baseframe import frame_transform_graph
from astropy.coordinates.transformations import FunctionTransform
from astropy.coordinates.representation import (SphericalRepresentation, CartesianRepresentation,
                                                UnitSphericalRepresentation)
from astropy.coordinates import (ICRS, GCRS, CIRS, HCRS, AltAz)
from astropy.coordinates.builtin_frames.utils import (get_jd12, get_cip, prepare_earth_position_vel,
                                                      PIOVER2, get_polar_motion, get_dut1utc, aticq, atciqz)


def get_astrom(frame, tcode, precision=600):
    """
    Get astrometry context for frame transformations with ERFA.

    This routine uses the fact that most of the parameters involved in
    frame transformation (e.g. the location of the Earth) are slowly
    changing, whereas the fastest changing parameter is the Earth Rotation
    Angle (ERA). Most transform parameters are calculated on a coarser
    time resolution, whereas ERA is calculated for every time in the
    input frame.

    Parameters
    ----------
    frame : `~astropy.coordinates.BaseFrame`
        The starting frame, which has associated `obstime`s.
    tcode : str
        The erfa transformation the astrometry context will be used in.
        Must be one of 'apio13', 'apci', 'apcs', 'apci13', 'apco13'.
    precision : float, default=600
        The precision with which to calculate astrometry contexts (except for ERA)
    """

    assert tcode in ['apio13', 'apci', 'apcs', 'apci13', 'apco13']

    if precision < 1.e-3:
        # below millisecond MJD resolution, one is probably better of
        # with no MJD binning
        mjd_resolution = None

    else:
        mjd_resolution = precision / 86400.  # in days

    obstime = frame.obstime
    jd1_tt, jd2_tt = get_jd12(obstime, 'tt')

    if mjd_resolution:
        # compute mjd support points for interpolation of Earth pv and cip
        mjd_lower = np.int64(obstime.mjd / mjd_resolution)
        mjd_upper = mjd_lower + 1
        mjd_u = np.unique([mjd_lower, mjd_upper])  # does sorting

        obstime_support = Time(mjd_u * mjd_resolution, format='mjd', scale=obstime.scale)

    if tcode in ['apci', 'apcs']:
        # find the position and velocity of earth
        if mjd_resolution:
            (
                earth_pv_support, earth_heliocentric_support
                ) = prepare_earth_position_vel(obstime_support)
            # do interpolation
            earth_pv = np.empty(obstime.shape + (2, 3,))
            earth_heliocentric = np.empty(obstime.shape + (3,))
            for dim in range(3):
                for ipv in range(2):
                    earth_pv[..., ipv, dim] = np.interp(
                        obstime.mjd, obstime_support.mjd, earth_pv_support[:, ipv, dim]
                        )
                earth_heliocentric[..., dim] = np.interp(
                    obstime.mjd, obstime_support.mjd, earth_heliocentric_support[:, dim]
                    )

        else:
            earth_pv, earth_heliocentric = prepare_earth_position_vel(obstime)

    if tcode == 'apio13':

        lon, lat, height = frame.location.to_geodetic('WGS84')

        xp, yp = get_polar_motion(obstime)
        jd1_utc, jd2_utc = get_jd12(obstime, 'utc')
        dut1utc = get_dut1utc(obstime)
        astrom = erfa.apio13(
            jd1_utc, jd2_utc, dut1utc,
            lon.to(u.radian).value, lat.to(u.radian).value,
            height.to(u.m).value,
            xp, yp,  # polar motion
            # all below are already in correct units because they are QuantityFrameAttribues
            frame.pressure.value,
            frame.temperature.value,
            frame.relative_humidity,
            frame.obswl.value
            )

    elif tcode == 'apci':

        if mjd_resolution:
            jd1_tt_support, jd2_tt_support = get_jd12(obstime_support, 'tt')
            cip_support = get_cip(jd1_tt_support, jd2_tt_support)
            # do interpolation; cip is an ordinary array
            cip = tuple(
                np.interp(obstime.mjd, obstime_support.mjd, cip_support[i])
                for i in range(3)
                )

        else:
            cip = get_cip(jd1_tt, jd2_tt)

        astrom = erfa.apci(jd1_tt, jd2_tt, earth_pv, earth_heliocentric, *cip)

    elif tcode == 'apcs':

        # get the position and velocity arrays for the observatory.  Need to
        # have xyz in last dimension, and pos/vel in one-but-last.
        # (Note could use np.stack once our minimum numpy version is >=1.10.)
        pv = np.concatenate(
                (frame.obsgeoloc.get_xyz(xyz_axis=-1).value[..., np.newaxis, :],
                 frame.obsgeovel.get_xyz(xyz_axis=-1).value[..., np.newaxis, :]),
                axis=-2)
        astrom = erfa.apcs(jd1_tt, jd2_tt, pv, earth_pv, earth_heliocentric)

    elif tcode == 'apci13':
        pass
    elif tcode == 'apco13':
        pass

    return astrom


@frame_transform_graph.transform(FunctionTransform, CIRS, AltAz)
def cirs_to_altaz(cirs_coo, altaz_frame):
    if np.any(cirs_coo.obstime != altaz_frame.obstime):
        # the only frame attribute for the current CIRS is the obstime, but this
        # would need to be updated if a future change allowed specifying an
        # Earth location algorithm or something
        cirs_coo = cirs_coo.transform_to(CIRS(obstime=altaz_frame.obstime))

    # we use the same obstime everywhere now that we know they're the same
    obstime = cirs_coo.obstime

    # if the data are UnitSphericalRepresentation, we can skip the distance calculations
    is_unitspherical = (isinstance(cirs_coo.data, UnitSphericalRepresentation) or
                        cirs_coo.cartesian.x.unit == u.one)

    if is_unitspherical:
        usrepr = cirs_coo.represent_as(UnitSphericalRepresentation)
        cirs_ra = usrepr.lon.to(u.radian).value
        cirs_dec = usrepr.lat.to(u.radian).value
    else:
        # compute an "astrometric" ra/dec -i.e., the direction of the
        # displacement vector from the observer to the target in CIRS
        loccirs = altaz_frame.location.get_itrs(cirs_coo.obstime).transform_to(cirs_coo)
        diffrepr = (cirs_coo.cartesian - loccirs.cartesian).represent_as(UnitSphericalRepresentation)

        cirs_ra = diffrepr.lon.to(u.radian).value
        cirs_dec = diffrepr.lat.to(u.radian).value

    #first set up the astrometry context for CIRS<->AltAz
    astrom = get_astrom(altaz_frame, 'apio13')

    az, zen, _, _, _ = erfa.atioq(cirs_ra, cirs_dec, astrom)

    if is_unitspherical:
        rep = UnitSphericalRepresentation(lat=u.Quantity(PIOVER2 - zen, u.radian, copy=False),
                                          lon=u.Quantity(az, u.radian, copy=False),
                                          copy=False)
    else:
        # now we get the distance as the cartesian distance from the earth
        # location to the coordinate location
        locitrs = altaz_frame.location.get_itrs(obstime)
        distance = locitrs.separation_3d(cirs_coo)
        rep = SphericalRepresentation(lat=u.Quantity(PIOVER2 - zen, u.radian, copy=False),
                                      lon=u.Quantity(az, u.radian, copy=False),
                                      distance=distance,
                                      copy=False)
    return altaz_frame.realize_frame(rep)


@frame_transform_graph.transform(FunctionTransform, AltAz, CIRS)
def altaz_to_cirs(altaz_coo, cirs_frame):
    usrepr = altaz_coo.represent_as(UnitSphericalRepresentation)
    az = usrepr.lon.to(u.radian).value
    zen = PIOVER2 - usrepr.lat.to(u.radian).value

    #first set up the astrometry context for ICRS<->CIRS at the altaz_coo time
    astrom = get_astrom(altaz_coo, 'apio13')

    # the 'A' indicates zen/az inputs
    cirs_ra, cirs_dec = erfa.atoiq('A', az, zen, astrom)*u.radian
    if isinstance(altaz_coo.data, UnitSphericalRepresentation) or altaz_coo.cartesian.x.unit == u.one:
        cirs_at_aa_time = CIRS(ra=cirs_ra, dec=cirs_dec, distance=None,
                               obstime=altaz_coo.obstime)
    else:
        # treat the output of atoiq as an "astrometric" RA/DEC, so to get the
        # actual RA/Dec from the observers vantage point, we have to reverse
        # the vector operation of cirs_to_altaz (see there for more detail)

        loccirs = altaz_coo.location.get_itrs(altaz_coo.obstime).transform_to(cirs_frame)

        astrometric_rep = SphericalRepresentation(lon=cirs_ra, lat=cirs_dec,
                                                  distance=altaz_coo.distance)
        newrepr = astrometric_rep + loccirs.cartesian
        cirs_at_aa_time = CIRS(newrepr, obstime=altaz_coo.obstime)

    #this final transform may be a no-op if the obstimes are the same
    return cirs_at_aa_time.transform_to(cirs_frame)


@frame_transform_graph.transform(FunctionTransform, AltAz, AltAz)
def altaz_to_altaz(from_coo, to_frame):
    # for now we just implement this through CIRS to make sure we get everything
    # covered
    return from_coo.transform_to(CIRS(obstime=from_coo.obstime)).transform_to(to_frame)

# First the ICRS/CIRS related transforms
@frame_transform_graph.transform(FunctionTransform, ICRS, CIRS)
def icrs_to_cirs(icrs_coo, cirs_frame):
    # first set up the astrometry context for ICRS<->CIRS
    astrom = get_astrom(cirs_frame, 'apci')

    if icrs_coo.data.get_name() == 'unitspherical' or icrs_coo.data.to_cartesian().x.unit == u.one:
        # if no distance, just do the infinite-distance/no parallax calculation
        usrepr = icrs_coo.represent_as(UnitSphericalRepresentation)
        i_ra = usrepr.lon.to(u.radian).value
        i_dec = usrepr.lat.to(u.radian).value
        cirs_ra, cirs_dec = erfa.atciqz(i_ra, i_dec, astrom)

        newrep = UnitSphericalRepresentation(lat=u.Quantity(cirs_dec, u.radian, copy=False),
                                             lon=u.Quantity(cirs_ra, u.radian, copy=False),
                                             copy=False)
    else:
        # When there is a distance,  we first offset for parallax to get the
        # astrometric coordinate direction and *then* run the ERFA transform for
        # no parallax/PM. This ensures reversibility and is more sensible for
        # inside solar system objects
        astrom_eb = CartesianRepresentation(astrom['eb'], unit=u.au,
                                            xyz_axis=-1, copy=False)
        newcart = icrs_coo.cartesian - astrom_eb

        srepr = newcart.represent_as(SphericalRepresentation)
        i_ra = srepr.lon.to(u.radian).value
        i_dec = srepr.lat.to(u.radian).value
        cirs_ra, cirs_dec = erfa.atciqz(i_ra, i_dec, astrom)

        newrep = SphericalRepresentation(lat=u.Quantity(cirs_dec, u.radian, copy=False),
                                         lon=u.Quantity(cirs_ra, u.radian, copy=False),
                                         distance=srepr.distance, copy=False)

    return cirs_frame.realize_frame(newrep)


@frame_transform_graph.transform(FunctionTransform, CIRS, ICRS)
def cirs_to_icrs(cirs_coo, icrs_frame):
    srepr = cirs_coo.represent_as(UnitSphericalRepresentation)
    cirs_ra = srepr.lon.to(u.radian).value
    cirs_dec = srepr.lat.to(u.radian).value

    # set up the astrometry context for ICRS<->cirs and then convert to
    # astrometric coordinate direction
    astrom = get_astrom(cirs_coo, 'apci')

    i_ra, i_dec = aticq(cirs_ra, cirs_dec, astrom)

    if cirs_coo.data.get_name() == 'unitspherical' or cirs_coo.data.to_cartesian().x.unit == u.one:
        # if no distance, just use the coordinate direction to yield the
        # infinite-distance/no parallax answer
        newrep = UnitSphericalRepresentation(lat=u.Quantity(i_dec, u.radian, copy=False),
                                             lon=u.Quantity(i_ra, u.radian, copy=False),
                                             copy=False)
    else:
        # When there is a distance, apply the parallax/offset to the SSB as the
        # last step - ensures round-tripping with the icrs_to_cirs transform

        # the distance in intermedrep is *not* a real distance as it does not
        # include the offset back to the SSB
        intermedrep = SphericalRepresentation(lat=u.Quantity(i_dec, u.radian, copy=False),
                                              lon=u.Quantity(i_ra, u.radian, copy=False),
                                              distance=cirs_coo.distance,
                                              copy=False)

        astrom_eb = CartesianRepresentation(astrom['eb'], unit=u.au,
                                            xyz_axis=-1, copy=False)
        newrep = intermedrep + astrom_eb

    return icrs_frame.realize_frame(newrep)


@frame_transform_graph.transform(FunctionTransform, CIRS, CIRS)
def cirs_to_cirs(from_coo, to_frame):
    if np.all(from_coo.obstime == to_frame.obstime):
        return to_frame.realize_frame(from_coo.data)
    else:
        # the CIRS<-> CIRS transform actually goes through ICRS.  This has a
        # subtle implication that a point in CIRS is uniquely determined
        # by the corresponding astrometric ICRS coordinate *at its
        # current time*.  This has some subtle implications in terms of GR, but
        # is sort of glossed over in the current scheme because we are dropping
        # distances anyway.
        return from_coo.transform_to(ICRS).transform_to(to_frame)


# Now the GCRS-related transforms to/from ICRS

@frame_transform_graph.transform(FunctionTransform, ICRS, GCRS)
def icrs_to_gcrs(icrs_coo, gcrs_frame):
    # first set up the astrometry context for ICRS<->GCRS. There are a few steps...
    astrom = get_astrom(gcrs_frame, 'apcs')

    if icrs_coo.data.get_name() == 'unitspherical' or icrs_coo.data.to_cartesian().x.unit == u.one:
        # if no distance, just do the infinite-distance/no parallax calculation
        usrepr = icrs_coo.represent_as(UnitSphericalRepresentation)
        i_ra = usrepr.lon.to(u.radian).value
        i_dec = usrepr.lat.to(u.radian).value
        gcrs_ra, gcrs_dec = atciqz(i_ra, i_dec, astrom)

        newrep = UnitSphericalRepresentation(lat=u.Quantity(gcrs_dec, u.radian, copy=False),
                                             lon=u.Quantity(gcrs_ra, u.radian, copy=False),
                                             copy=False)
    else:
        # When there is a distance,  we first offset for parallax to get the
        # BCRS coordinate direction and *then* run the ERFA transform for no
        # parallax/PM. This ensures reversibility and is more sensible for
        # inside solar system objects
        astrom_eb = CartesianRepresentation(astrom['eb'], unit=u.au,
                                            xyz_axis=-1, copy=False)
        newcart = icrs_coo.cartesian - astrom_eb

        srepr = newcart.represent_as(SphericalRepresentation)
        i_ra = srepr.lon.to(u.radian).value
        i_dec = srepr.lat.to(u.radian).value
        gcrs_ra, gcrs_dec = atciqz(i_ra, i_dec, astrom)

        newrep = SphericalRepresentation(lat=u.Quantity(gcrs_dec, u.radian, copy=False),
                                         lon=u.Quantity(gcrs_ra, u.radian, copy=False),
                                         distance=srepr.distance, copy=False)

    return gcrs_frame.realize_frame(newrep)


@frame_transform_graph.transform(FunctionTransform, GCRS, ICRS)
def gcrs_to_icrs(gcrs_coo, icrs_frame):
    srepr = gcrs_coo.represent_as(UnitSphericalRepresentation)
    gcrs_ra = srepr.lon.to(u.radian).value
    gcrs_dec = srepr.lat.to(u.radian).value

    # set up the astrometry context for ICRS<->GCRS and then convert to BCRS
    # coordinate direction
    astrom = get_astrom(gcrs_coo, 'apcs')

    i_ra, i_dec = aticq(gcrs_ra, gcrs_dec, astrom)

    if gcrs_coo.data.get_name() == 'unitspherical' or gcrs_coo.data.to_cartesian().x.unit == u.one:
        # if no distance, just use the coordinate direction to yield the
        # infinite-distance/no parallax answer
        newrep = UnitSphericalRepresentation(lat=u.Quantity(i_dec, u.radian, copy=False),
                                             lon=u.Quantity(i_ra, u.radian, copy=False),
                                             copy=False)
    else:
        # When there is a distance, apply the parallax/offset to the SSB as the
        # last step - ensures round-tripping with the icrs_to_gcrs transform

        # the distance in intermedrep is *not* a real distance as it does not
        # include the offset back to the SSB
        intermedrep = SphericalRepresentation(lat=u.Quantity(i_dec, u.radian, copy=False),
                                              lon=u.Quantity(i_ra, u.radian, copy=False),
                                              distance=gcrs_coo.distance,
                                              copy=False)

        astrom_eb = CartesianRepresentation(astrom['eb'], unit=u.au,
                                            xyz_axis=-1, copy=False)
        newrep = intermedrep + astrom_eb

    return icrs_frame.realize_frame(newrep)


@frame_transform_graph.transform(FunctionTransform, GCRS, GCRS)
def gcrs_to_gcrs(from_coo, to_frame):
    if (np.all(from_coo.obstime == to_frame.obstime)
        and np.all(from_coo.obsgeoloc == to_frame.obsgeoloc)):
        return to_frame.realize_frame(from_coo.data)
    else:
        # like CIRS, we do this self-transform via ICRS
        return from_coo.transform_to(ICRS).transform_to(to_frame)


@frame_transform_graph.transform(FunctionTransform, GCRS, HCRS)
def gcrs_to_hcrs(gcrs_coo, hcrs_frame):

    if np.any(gcrs_coo.obstime != hcrs_frame.obstime):
        # if they GCRS obstime and HCRS obstime are not the same, we first
        # have to move to a GCRS where they are.
        frameattrs = gcrs_coo.get_frame_attr_names()
        frameattrs['obstime'] = hcrs_frame.obstime
        gcrs_coo = gcrs_coo.transform_to(GCRS(**frameattrs))

    srepr = gcrs_coo.represent_as(UnitSphericalRepresentation)
    gcrs_ra = srepr.lon.to(u.radian).value
    gcrs_dec = srepr.lat.to(u.radian).value

    # set up the astrometry context for ICRS<->GCRS and then convert to ICRS
    # coordinate direction
    astrom = get_astrom(gcrs_coo, 'apcs')

    i_ra, i_dec = aticq(gcrs_ra, gcrs_dec, astrom)

    # convert to Quantity objects
    i_ra = u.Quantity(i_ra, u.radian, copy=False)
    i_dec = u.Quantity(i_dec, u.radian, copy=False)
    if gcrs_coo.data.get_name() == 'unitspherical' or gcrs_coo.data.to_cartesian().x.unit == u.one:
        # if no distance, just use the coordinate direction to yield the
        # infinite-distance/no parallax answer
        newrep = UnitSphericalRepresentation(lat=i_dec, lon=i_ra, copy=False)
    else:
        # When there is a distance, apply the parallax/offset to the
        # Heliocentre as the last step to ensure round-tripping with the
        # hcrs_to_gcrs transform

        # Note that the distance in intermedrep is *not* a real distance as it
        # does not include the offset back to the Heliocentre
        intermedrep = SphericalRepresentation(lat=i_dec, lon=i_ra,
                                              distance=gcrs_coo.distance,
                                              copy=False)

        # astrom['eh'] and astrom['em'] contain Sun to observer unit vector,
        # and distance, respectively. Shapes are (X) and (X,3), where (X) is the
        # shape resulting from broadcasting the shape of the times object
        # against the shape of the pv array.
        # broadcast em to eh and scale eh
        eh = astrom['eh'] * astrom['em'][..., np.newaxis]
        eh = CartesianRepresentation(eh, unit=u.au, xyz_axis=-1, copy=False)

        newrep = intermedrep.to_cartesian() + eh

    return hcrs_frame.realize_frame(newrep)
