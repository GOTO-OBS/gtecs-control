"""Classes to control ASA mounts using TCP commands."""

import logging
import socket
import threading
import time
from enum import Enum

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

from ...astronomy import apparent_to_j2000, j2000_to_apparent


class Commands(Enum):
    NONE = 0
    ERRORCMD = 1
    WARNINGCMD = 2
    ABORTINIT = 3
    ABORTCMD = 4

    SIMULOFFSET = 5

    TELCONNECT = 6
    TELCONNECTASYNC = 7
    TELDISCONNECT = 8
    TELCONNECTED = 9
    TELINIT = 10
    TELSTOP = 11
    TELSETPARKPOS = 12
    TELSTATUS = 13
    TELEVENT = 14
    TELSYNC2ZENITH = 15
    TELSYNC2IMAGE = 16
    TELHALT = 17
    TELSTARTMOTORS = 18
    TELSERIESSTATUS = 19
    TELSERIESABORT = 20
    TELSLEWTOSTARASYNC = 21
    TELSLEWTOAZELEASYNC = 22
    TELSLEWTOHADECASYNC = 23
    TELSLEWTOSATELLITEASYNC = 24
    TELSLEWTOCOORDINATESASYNC = 25
    TELSLEWTOTARGETASYNC = 26

    SETGPSLOCATION = 27
    GETGPSSTATUS = 28
    SAVELOCATION = 29
    SAVEATMOSPHERE = 30

    MOUNTCONNECT = 31
    MOUNTDISCONNECT = 32
    MOUNTCONNECTED = 33
    MOUNTINIT = 34
    MOUNTRESET = 35
    MOUNTRESETTODEFAULT = 36
    MOUNTMOTORSSTART = 37
    MOUNTMOTORSSTOP = 38
    MOUNTSLEWTOSTAR = 39
    MOUNTSLEWTOSTARASYNC = 40
    MOUNTSLEWTOHADEC = 41
    MOUNTSLEWTOHADECASYNC = 42
    MOUNTSLEWTOSATELLITE = 43
    MOUNTSLEWTOSATELLITEASYNC = 44
    MOUNTSLEWTOTARGET = 45
    MOUNTSLEWTOTARGETASYNC = 46
    MOUNTSLEWTOAZELE = 47
    MOUNTSLEWTOAZELEASYNC = 48
    MOUNTSLEWTOCOORDINATES = 49
    MOUNTSLEWTOCOORDINATESASYNC = 50

    GETMAXRATE = 51
    SETMAXRATE = 52
    ABORTSLEW = 53
    ABORTTRACKING = 54
    CORRECTMULTITURN = 55
    DECLINATION = 56
    DECLINATIONRATE = 57
    FINDHOME = 58
    MOUNTSTATUS = 59
    MOVEAXIS = 60
    MOVEAXES12 = 61
    CANMOVEAXIS = 62
    FINDHOMEINDEX = 63
    FINDHOMEINDEXASYNC = 64
    FINDMAGANGLEOFFSET = 65
    FINDMAGANGLEOFFSETASYNC = 66
    PARK = 67
    SETPARK = 68
    UNPARK = 69
    RIGHTASCENSION = 70
    RIGHTASCENSIONRATE = 71
    SIDEOFPIER = 72
    DESTINATIONSIDEOFPIER = 73
    SLEWING = 74
    TRACKING = 75
    SLEWINGTIME = 76
    SYNCTOCOORDINATES = 77
    SYNCTOTARGET = 78
    SYNCTOZENITH = 79
    SYNCTOALTAZ = 80
    SYNCTOHORIZON = 81
    CORRECTZEROOFFSET = 82
    SAVEZEROOFFSETS = 83
    AZIMUTH = 84
    ELEVATION = 85
    SITEELEVATION = 86
    SITELATITUDE = 87
    SITELONGITUDE = 88
    SETTARGETRIGHTASCENSION = 89
    SETTARGETDECLINATION = 90
    GETTARGETRIGHTASCENSION = 91
    GETTARGETDECLINATION = 92
    SLEWABSOLUTE = 93
    SLEWABSOLUTEASYNC = 94
    SLEWRELATIVE = 95
    SLEWRELATIVEASYNC = 96
    GETPROPERTY = 97
    SETPROPERTY = 98
    SETPID = 99
    GETPID = 100
    GETSTATUS = 101
    SETMOTORPARAMETERS = 102
    SAVEMOTORPARAMETERS = 103
    GETMOTORPARAMETERS = 104
    GETAXISMAXLIMIT = 105
    SETAXISMAXLIMIT = 106
    SETAXISMAXLIMITTOCURRENTPOS = 107
    GETAXISMINLIMIT = 108
    SETAXISMINLIMIT = 109
    SETAXISMINLIMITTOCURRENTPOS = 110
    SETSWAPLIMITEASTTOCURRENTPOS = 111
    SETSWAPLIMITWESTTOCURRENTPOS = 112
    SETSTOPPERPOSTOCURRENTPOS = 113
    GETSTOPPERPOSITION = 114
    SAVELIMITS = 115
    SAVESWAPMODE = 116
    ERRORCLEAR = 117
    WARNINGCLEAR = 118
    ERRORRAISED = 119
    WARNINGRAISED = 120

    TMCONNECT = 121
    TMDISCONNECT = 122
    TMCONNECTED = 123
    TMPOSITION = 124

    ACCCONNECT = 125
    ACCDISCONNECT = 126
    ACCCONNECTED = 127
    ACCNUMBEROFCONTROLLERS = 128
    ACCNUMBEROFDEVICES = 129
    ACCNUMBEROFFOCUSERS = 130
    ACCNUMBEROFROTATORS = 131
    ACCNUMBEROFCOVERS = 132
    ACCNUMBEROFFILTERWHEELS = 133
    ACCSETNUMBEROFFOCUSERAXES = 134
    ACCGETNUMBEROFFOCUSERAXES = 135
    ACCSETNUMBEROFCOVERAXES = 136
    ACCGETNUMBEROFCOVERAXES = 137
    ACCSETNUMBEROFFILTERS = 138
    ACCGETNUMBEROFFILTERS = 139
    ACCFOCUSERISABSOLUTE = 140
    ACCFOCUSERCANSLEWABSOLUTE = 141
    ACCFOCUSERINIT = 142
    ACCFOCUSERINITASYNC = 143
    ACCFOCUSERMOVE = 144
    ACCFOCUSERMOVEASYNC = 145
    ACCSETFOCUSMODEL = 146
    ACCGETFOCUSMODEL = 147
    ACCFOCUSERMOVETOMODELPOS = 148
    ACCFOCUSERMOVETOMODELPOSASYNC = 149
    ACCFOCUSERPOSITION = 150
    ACCFOCUSERHALT = 151
    ACCFOCUSERISMOVING = 152
    ACCFOCUSERTEMP = 153
    ACCSETFOCUSOFFSET = 154
    ACCGETFOCUSOFFSET = 155
    ACCFOCUSERTEST = 156
    ACCCOVERISABSOLUTE = 157
    ACCCOVERINIT = 158
    ACCCOVERINITASYNC = 159
    ACCCOVERMOVE = 160
    ACCCOVEROPEN = 161
    ACCCOVERCLOSE = 162
    ACCCOVEROPENASYNC = 163
    ACCCOVERCLOSEASYNC = 164
    ACCCOVERHALT = 165
    ACCCOVERISMOVING = 166
    ACCCOVERPOSITION = 167
    ACCCOVERSTATUS = 168
    ACCCOVERERRORSTATUS = 169
    ACCROTATORISABSOLUTE = 170
    ACCROTATORINIT = 171
    ACCROTATORINITASYNC = 172
    ACCROTATORMOVE = 173
    ACCROTATORSETPOSITION = 174
    ACCROTATORGETPOSITION = 175
    ACCROTATORHALT = 176
    ACCROTATORISMOVING = 177
    ACCROTATORTEMP = 178
    ACCROTATORSTATUS = 179
    ACCFILTERON = 180
    ACCFILTEROFF = 181
    ACCFILTERPOSITION = 182
    ACCFIRMWARE = 183

    FOCUSCONNECT = 184
    FOCUSDISCONNECT = 185
    FOCUSCONNECTED = 186
    FOCUSINIT = 187
    FOCUSMOVE = 188
    FOCUSMOVEASYNC = 189
    FOCUSPOSITION = 190
    FOCUSHALT = 191
    FOCUSISMOVING = 192
    FOCUSTEMP = 193

    FILCONNECT = 194
    FILDISCONNECT = 195
    FILCONNECTED = 196
    FILINIT = 197
    FILMOVE = 198
    FILMOVEASYNC = 199
    FILPOSITION = 200
    FILSTATUS = 201
    FILISMOVING = 202

    DOMECONNECT = 203
    DOMEDISCONNECT = 204
    DOMECONNECTED = 205
    DOMERESET = 206
    DOMEOPEN = 207
    DOMECLOSE = 208
    DOMESTOP = 209
    DOMESLEWTOALTITUDE = 210
    DOMESLEWTOAZIMUTH = 211
    DOMESYNCTOAZIMUTH = 212
    DOMEFINDHOME = 213
    DOMEPARK = 214
    DOMEABORTSLEW = 215
    DOMESLEWING = 216
    DOMEONPOSITION = 217
    DOMEAZIMUTH = 218
    DOMESETPARK = 219

    CAMCONNECT = 220
    CAMDISCONNECT = 221
    CAMCONNECTED = 222
    SETIMAGEFORMAT = 223
    GETIMAGEFORMAT = 224
    SETGENERAL = 225
    SETWINDOW = 226
    ACQUIRE = 227
    ACQUIREASYNC = 228
    ACQUIRESTATUS = 229
    ACQUIRESERIES = 230
    ACQUIRESTOP = 231
    GETTEMP = 232
    SETTEMP = 233
    SETTEMPASYNC = 234
    READIOPORT = 235
    WRITEIOPORT = 236
    SENDIMAGE = 237
    SHUTTERSPEEDS = 238
    GAINS = 239
    SETGAIN = 240
    GETGAIN = 241

    ETCONNECT = 242
    ETDISCONNECT = 243
    ETCONNECTED = 244
    ETSETMODE = 245
    ETREADTIME = 246
    ETREADEVENTTIME = 247
    SENDEXPTIMES = 248
    RESETREADEXPTIMES = 249
    SENDSTART = 250
    SENDSTOP = 251
    SENDEND = 252
    STOPEND = 253
    SENDSTATUS = 254
    STOPSTATUS = 255

    UCAC2 = 256
    UCAC3 = 257
    UCAC4 = 258
    TYCHO2 = 259
    HIPPARCOS = 260

    ACQUIRESTARSERIES = 261
    ACQUIRESATSERIES = 262
    POINTINGFILE = 263
    PLATESOLVE = 264
    SAVEPOINTINGOFFSET = 265
    GETPOINTINGOFFSET = 266
    READCONFIGURATIONS = 267
    CALCULATEPOINTINGMODEL = 268
    READCURRENTPOINTINGMODEL = 269
    SAVEPOINTINGMODEL = 270
    READPOINTINGMODEL = 271
    SELECTPOINTINGMODEL = 272
    SELECTDEFAULTPOINTINGMODEL = 273
    GETPOINTINGCORRECTION = 274

    CCDREQUEST = 275
    CCDSTART = 276
    FOCPOS = 277
    FOCSTATUS = 278
    FOCCONNECT = 279
    FOCDISCONNECT = 280
    FOCINIT = 281
    FILPOS = 282
    CAMOPEN = 283
    CAMCLOSE = 284
    SETUSERBIT = 285
    WDAC = 286
    READVALUE = 287
    READEVENT = 288
    SEND = 289
    BLACKLEVEL = 290
    TELSTAR = 291
    TELPOS = 292
    TELEPH = 293
    TELPARK = 294
    TELVEL = 295
    TELMOTORON = 296
    TELMOTOROFF = 297
    TELMAGANGLEFIND = 298
    TELFINDHOME = 299
    BCOPEN = 300
    BCCLOSE = 301
    BCSETMODE = 302
    BCREADTIME = 303
    READEVENTTIME = 304
    READEXPTIMES = 305
    STRNG = 306

    GETSATDATA = 307
    METEOCONNECT = 308
    METEODISCONNECT = 309
    METEOCONNECTED = 310
    GETOBSCONDITIONS = 311
    SHUTTEROPEN = 312
    SHUTTERCLOSE = 313
    SHUTTERSTATUS = 314
    READOUTSPEEDS = 315
    GETTRACKINGDATA = 316
    DOMEOPENASYNC = 317
    DOMECLOSEASYNC = 318
    SYNCPRIMARYAXIS = 319
    SYNCSECONDARYAXIS = 320
    ABORT = 321
    TOGGLERELATIVE = 322
    TOGGLERELATIVEASYNC = 323
    GETMAXPID = 324
    CFGFILES = 325
    SETTELMODULE = 326
    READTELMODULE = 327

    STARTOBS = 328
    RESTARTOBS = 329
    STOPOBS = 330
    TERMINATEOBS = 331
    GETOBSPERFORMED = 332
    CLEARTARGETQUEUE = 333
    CLEARTARGETLIST = 334
    REMOVETARGETFROMQUEUE = 335
    REMOVETARGETFROMLIST = 336
    ADDTARGETTOQUEUE = 337
    ADDTARGETTOLIST = 338
    GETTARGETQUEUE = 339
    GETTARGETLIST = 340
    SERIESSTART = 341
    SERIESSTOP = 342
    SERIESSTATUS = 343

    SOFTWAREVERSION = 344
    TCPVERSION = 345
    GETSATAZELE = 346
    LOADTLELIST = 347
    GETTLELIST = 348
    PULSEGUIDE = 349
    ACCSETROTATORMOTORDIR = 350
    ACCGETROTATORMOTORDIR = 351
    SAVEROTATORMODE = 352
    ACCSETROTATOROFFSET = 353
    ACCGETROTATOROFFSET = 354
    ACCROTATORGETPOSITIONABSOLUTE = 355
    ACCSETROTATORDEFAULTPOSITION = 356
    ACCGETROTATORDEFAULTPOSITION = 357
    SAVETELCONFIG = 358
    READCURRENTTELCONFIG = 359
    READTELCONFIG = 360
    SETTELCONFIG = 361
    READCURRENTTELCONFIGNAME = 362
    SAVETMPOSITION = 363
    SAVEDOMECONFIG = 364
    READTELCONFIGLIST = 365
    READTELMODULELIST = 366
    READCURRENTTELMODULENAME = 367
    SAVETELMODULE = 368
    EOPFILE = 369
    EOPFILENAME = 370
    JPLFILE = 371
    JPLFILENAME = 372
    TIMESYNCINFO = 373
    ADDTIMESERVER = 374
    DELETETIMESERVER = 375
    READTIMESERVERLIST = 376
    READACCMODULELIST = 377
    READCURRENTACCMODULENAME = 378
    READCURRENTACCMODULE = 379
    SAVEACCMODULE = 380
    SETACCMODULE = 381
    READACCMODULE = 382
    READACCCONFIGLIST = 383
    READCURRENTACCCONFIGNAME = 384
    SAVEACCCONFIG = 385
    SETACCCONFIG = 386
    READACCCONFIG = 387
    READDEFAULTTELCONFIGNAME = 388
    ACCFILTERFOCUSOFFSET = 389
    ACCFILTERNAME = 390
    ACCROTATORSETPOSITIONABSOLUTE = 391
    SCANFOROBJECTS = 392
    READIMAGEFILES = 393
    GETMOUNTMOTORCONTROLLERLIMITS = 394
    SETMOUNTMOTORCONTROLLERLIMITS = 395
    GETNOHALLPARAMETERS = 396
    SAVENOHALLPARAMETERS = 397
    FINDMAGANGLEOFFSETSTATUS = 398
    SAVEMAGANGLEOFFSET = 399
    ACCFIRMWAREDEVICES = 400
    ACCDEVICELIST = 401

    MOUNTSLEWTOOBJECTASYNC = 402
    TELSLEWTOOBJECTASYNC = 403
    UPDATEPVT = 404
    GETACCSTATUS = 405
    SETLDRTHRESHOLD = 406
    CORRECTPOINTINGMODEL = 407
    DELETEPOINTINGMODEL = 408
    CAMERALIST = 409
    SETOUTPRESSURE = 410
    SETOUTHUMIDITY = 411
    SETOUTTEMPERATURE = 412
    DOMEFANONOFF = 413
    DOMEHOME = 414
    DOMEFIRMWARE = 415
    DOMESHUTTERFIRMWARE = 416
    DOMELIGHTONOFF = 417
    DOMEMOVE = 418
    DOMEMOVECW = 419
    DOMEMOVECCW = 420

    GETMOUNTCONTROLLERLOGS = 421
    GETMOUNTCONTROLLERLOGFILE = 422
    GETTMCONTROLLERLOGS = 423
    GETTMCONTROLLERLOGFILE = 424
    GETDOMECONTROLLERLOGS = 425
    GETDOMECONTROLLERLOGFILE = 426
    GETACCCONTROLLERLOGS = 427
    GETACCCONTROLLERLOGFILE = 428
    GETCAMERACONTROLLERLOGS = 429
    GETCAMERACONTROLLERLOGFILE = 430
    GETTRACKINGLOGS = 431
    GETTRACKINGLOGFILE = 432
    GETIMAGEFILE = 433

    TMPOSITIONASYNC = 434
    ISGPSINSTALLED = 435
    GETGPSLOCATION = 436
    GETGSAFRAME = 437
    GETGGAFRAME = 438

    ACCGETNUMBEROFCOVERBLADES = 439
    ACCSETNUMBEROFCOVERBLADES = 440

    GETTMMOTORCONTROLLERLIMITS = 441
    SETTMMOTORCONTROLLERLIMITS = 442
    SETSTOPPERPOSITION = 443

    TLELIST = 444
    OBSPLAN = 445
    GETOBSPLANS = 446
    GETCURRENTOBSPLAN = 447
    DELETEOBSPLAN = 448
    READOBSPLAN = 449
    READSCHEDULERCONFIGLIST = 450
    READSCHEDULERCONFIG = 451
    SAVESCHEDULERCONFIG = 452
    DELETESCHEDULERCONFIG = 453
    DOMESHUTTERFINDHOME = 454

    UNKNOWN = 455


class Keywords(Enum):
    """Command keyword status code"""
    ERRORMSG = 32
    WARNINGMSG = 33
    INFOMSG = 34
    ERRORRAISED = 35
    WARNINGRAISED = 36

    DEBUGON = 37
    LOGMODE = 38
    LOGON = 39
    LOGINTERVAL = 40
    PROPERTY = 41

    TELTIME = 42
    DEC_TARGET = 43
    RA_TARGET = 44
    ELE_TARGET = 45
    AZ_TARGET = 46
    PIERSIDE_TARGET = 47
    DEC_RATE = 48
    RA_RATE = 49
    PIERSIDE = 50
    TELMODE = 51
    RIGHTASCENSION = 52
    DECLINATION = 53
    HOURANGLE = 54
    REFSYSTEM = 55
    AZIMUTH = 56
    ELEVATION = 57
    TARGETAZIMUTH = 58
    TARGETELEVATION = 59
    UTCPOSREF1 = 60
    UTCPOSREF2 = 61
    UTCPOSITION1 = 62
    UTCPOSITION2 = 63
    POSITION1 = 64
    POSITION2 = 65
    VELOCITY1 = 66
    VELOCITY2 = 67
    ACCELERATION1 = 68
    ACCELERATION2 = 69
    CURRENTQ1 = 70
    CURRENTQ2 = 71
    ENCODEROFFSET1 = 72
    ENCODEROFFSET2 = 73
    ENCODEROFFSET3 = 74
    SYNCOFFSET1 = 75
    SYNCOFFSET2 = 76
    SYNCOFFSET3 = 77
    POSITIONERROR1 = 78
    POSITIONERROR2 = 79
    TRACKINGERROR1 = 80
    TRACKINGERROR2 = 81
    TRACKINGERROR3 = 82
    REFRACTION1 = 83
    REFRACTION2 = 84
    MOUNTCORR1 = 85
    MOUNTCORR2 = 86
    MOUNTCALIB1 = 87
    MOUNTCALIB2 = 88
    TARGETPOSITION1 = 89
    TARGETPOSITION2 = 90
    ISCALIBRATED = 91
    ISABSOLUTE = 92
    AXIS = 93
    RATE = 94
    RATE1 = 95
    RATE2 = 96
    MAGANGLEOFFSET = 97
    WAY = 98
    TIMENEEDED = 99
    CANMOVEAXIS = 100
    TELMOTORON = 101
    TELMOTORONMODE = 102

    ALIGNMENTMODE = 103
    APERTUREAREA = 104
    APERTUREDIAMETER = 105
    ATHOME = 106
    ATPARK = 107
    PARKREQUESTED = 108
    CANFINDHOME = 109
    CANPARK = 110
    CANPULSEGUIDE = 111
    CANSETDECLINATIONRATE = 112
    CANSETGUIDERATES = 113
    CANSETPARK = 114
    CANSETPIERSIDE = 115
    CANSETRIGHTASCENSIONRATE = 116
    CANSETTRACKING = 117
    CANSLEW = 118
    CANSLEWALTAZ = 119
    CANSLEWALTAZASYNC = 120
    CANSLEWASYNC = 121
    CANSYNC = 122
    CANSYNCALTAZ = 123
    CANUNPARK = 124
    DOESREFRACTION = 125
    EQUATORIALSYSTEM = 126
    FOCALLENGTH = 127
    GUIDERATEDECLINATION = 128
    GUIDERATERIGHTASCENSION = 129
    ISPULSEGUIDING = 130
    GUIDEDIRECTION = 131
    DURATION = 132

    SIDEREALTIME = 133
    SITEELEVATION = 134
    SITELATITUDE = 135
    SITELONGITUDE = 136
    TELSLEWING = 137
    SLEWSETTLETIME = 138
    TELTRACKING = 139
    INITIALIZING = 140
    MOTORSON = 141
    FANSPEED = 142
    LASERON = 143
    PID_P = 144
    PID_I = 145
    PID_D = 146
    CI = 147
    CP = 148
    MOTORFILTER = 149
    PIDTYPE = 150
    FILTK = 151
    FILTVELMEAS = 152
    FILTTZ = 153
    FILTDZ = 154
    FILTWZ = 155
    FILTTP = 156
    FILTDP = 157
    FILTWP = 158
    MOTIONMODE = 159
    SWAPLIMITEAST = 160
    SWAPLIMITWEST = 161
    SWAPMODE = 162
    ENCODERVALUE = 163
    ZMAXLIMIT = 164
    MINDISTANCE = 165
    CHECKLIMITS = 166
    MANCORRX = 167
    MANCORRY = 168
    TIMEBIAS = 169
    GUIDERATEPRIMARYAXIS = 170
    GUIDERATESECONDARYAXIS = 171
    GUIDERATERA = 172
    GUIDERATEDEC = 173
    OFFSETRATEPRIMARYAXIS = 174
    OFFSETRATESECONDARYAXIS = 175
    OFFSETRATERA = 176
    OFFSETRATEDEC = 177
    MOUNTMODELFILENAME = 178
    CONFIGNAME = 179
    POSITIONERROR = 180
    TRACKINGERROR = 181
    MAXPOSITIONERROR = 182
    MAXTRACKINGERROR = 183
    RNDCTR = 184
    SYNCOFFSET = 185
    OSCILLATORCORRECTION = 186
    OSCILLATORTIMEDIFF = 187
    ERRORSTATUS = 188
    PERIPHERIALSTATUS = 189
    ENCODERPOSITION = 190
    TMPOSITION = 191

    CAMERAMODEL = 192
    CAMERASERIALNUMBER = 193
    XPIXELSIZE = 194
    YPIXELSIZE = 195
    XBIN = 196
    YBIN = 197
    XSIZE = 198
    YSIZE = 199
    XMIN = 200
    YMIN = 201
    CHIPTEMP = 202
    BASETEMP = 203
    COOLERPOWER = 204
    IMAGE = 205
    IMAGEFORMAT = 206
    IMAGESIZE = 207
    OBSTIME = 208
    EXPTIME = 209
    EXPCOUNT = 210
    INPUTNUMBER = 211
    INPUTMODE = 212
    XOFFSET = 213
    YOFFSET = 214
    XREF = 215
    YREF = 216
    flushratio = 217
    flushnumber = 218
    USEELECTRONICSHUTTER = 219
    SMEARBEFORE = 220
    SMEARAFTER = 221
    IOPORTSET = 222
    PIXELRATE = 223
    SENSITIVITY = 224
    SHUTTERMODE = 225
    GAIN = 226
    STARTTIME = 227
    ENDTIME = 228
    FILENAME = 229
    OBSNAME = 230
    TIMELEFT = 231
    NROWSREAD = 232
    TERMINATED = 233
    SHUTTEROPENTIME = 234
    SHUTTERCLOSETIME = 235
    PLATESOLVE = 236

    NUMBEROFCONTROLLERS = 237
    CONTROLLERNUMBER = 238
    NUMBEROFFOCUSERS = 239
    NUMBEROFCOVERS = 240
    NUMBEROFROTATORS = 241
    NUMBEROFFILTERWHEELS = 242
    NUMBEROFAXES = 243

    POSITION = 244
    MOVING = 245
    ONPOSITION = 246
    TEMPERATURE = 247
    TEMPCOEFF = 248
    MAXWAY = 249
    HOMEDIR = 250
    CANSLEWABSOLUTE = 251
    COVERSTATUS = 252
    OFFSET = 253

    DOMECANFINDHOME = 254
    DOMECANPARK = 255
    DOMECANSETALTITUDE = 256
    DOMECANSETAZIMUTH = 257
    DOMECANSETPARK = 258
    DOMECANSETSHUTTER = 259
    DOMECANSLAVE = 260
    DOMECANSYNCAZIMUTH = 261
    DOMESHUTTERSTATE = 262
    DOMESLAVED = 263
    DOMESLEWING = 264
    DOMEATHOME = 265
    DOMEATPARK = 266
    DOMEAZIMUTH = 267
    DOMEALTITUDE = 268
    DOMECONNECTED = 269

    CMDSTATUS = 270
    CONNECTED = 271
    SIMULOFFSET = 272
    DEVICENUMBER = 273
    TIMETAG = 274

    SERIESSTATUS = 275
    SERIESCOUNT = 276
    SERIESNUMBER = 277

    POINTINGDISTANCE = 278
    POINTINGFILENAME = 279
    POINTINGPARAMETER = 280
    POINTINGMODEL = 281
    POINTINGFILETYPE = 282
    RMS_POINTING = 283
    RMS_POINTING_1 = 284
    RMS_POINTING_2 = 285
    RMS_POINTING_OFFSET_1 = 286
    RMS_POINTING_OFFSET_2 = 287
    RMS_POINTING_POLE_AZIMUTH = 288
    RMS_POINTING_POLE_ALTITUDE = 289
    RMS_POINTING_SWAP_1 = 290
    RMS_POINTING_SWAP_2 = 291
    RMS_POINTING_CENTER_COS_1_1 = 292
    RMS_POINTING_CENTER_COS_2_2 = 293
    RMS_POINTING_CENTER_SIN_1_1 = 294
    RMS_POINTING_CENTER_SIN_2_2 = 295
    RMS_POINTING_CENTER_2COS_1_1 = 296
    RMS_POINTING_CENTER_2SIN_1_1 = 297
    RMS_POINTING_CENTER_2COS_2_1 = 298
    RMS_POINTING_CENTER_2SIN_2_1 = 299
    RMS_POINTING_CENTER_2COS_2_2 = 300
    RMS_POINTING_CENTER_2SIN_2_2 = 301
    RMS_POINTING_CENTER_2COS_1_2 = 302
    RMS_POINTING_CENTER_2SIN_1_2 = 303
    RMS_POINTING_COLLIMATION = 304
    RMS_POINTING_NONPERPENDICULAR = 305
    RMS_POINTING_FLEX = 306
    POINTING_OFFSET_1 = 307
    POINTING_OFFSET_2 = 308
    POINTING_POLE_AZIMUTH = 309
    POINTING_POLE_ALTITUDE = 310
    POINTING_SWAP_1 = 311
    POINTING_SWAP_2 = 312
    POINTING_CENTER_COS_1_1 = 313
    POINTING_CENTER_COS_2_2 = 314
    POINTING_CENTER_SIN_1_1 = 315
    POINTING_CENTER_SIN_2_2 = 316
    POINTING_CENTER_2COS_1_1 = 317
    POINTING_CENTER_2SIN_1_1 = 318
    POINTING_CENTER_2COS_2_1 = 319
    POINTING_CENTER_2SIN_2_1 = 320
    POINTING_CENTER_2COS_2_2 = 321
    POINTING_CENTER_2SIN_2_2 = 322
    POINTING_CENTER_2COS_1_2 = 323
    POINTING_CENTER_2SIN_1_2 = 324
    POINTING_COLLIMATION = 325
    POINTING_NONPERPENDICULAR = 326
    POINTING_FLEX = 327

    EPHEMERISFILENAME = 328
    STRINGLIST = 329

    OUTTEMP = 330
    OUTPRESS = 331
    OUTHUMID = 332

    UTC = 333
    EVENTTIME = 334
    EVENTSTARTTIME = 335
    EVENTENDTIME = 336
    ETSTATUS = 337
    ETMODE = 338
    SHUTTERTIME = 339
    STATUSTIME = 340

    HOSTNAME = 341
    TCPPORT = 342
    LOGPATH = 343

    FOCPOS = 344
    FILPOS = 345
    REFRACTION = 346
    TRACKINGRATE = 347
    TIMEOUT = 348
    FOPEN = 349
    BLACKLEVEL = 350
    CONFIGFILE = 351
    CCDINDEX = 352
    ADDRESS = 353
    VALUE = 354
    BITNO = 355
    WINDOWINDEX = 356
    PORT = 357
    SMEARCYCLE = 358
    SMEAREND = 359
    CONTFLUSH = 360
    SHUTTERT1 = 361
    SHUTTERT2 = 362
    VID = 363
    VIDEOMODE = 364
    TABLE = 365
    CAMERAMSG = 366

    RANGE = 367
    RANGE_RATE = 368
    AZIMUTH_RATE = 369
    ELEVATION_RATE = 370
    ISSAVE = 371
    VERSION = 372
    DOMEINITIALIZED = 373
    SIMULATE = 374
    SITEX = 375
    SITEY = 376
    SITEZ = 377
    ACCDEVICENAME = 378
    ACCCONTROLLERNAME = 379
    ACCHOSTNAME = 380
    ACCPORTNUMBER = 381
    DOMEDEVICENAME = 382
    DOMECONTROLLERNAME = 383
    DOMEHOSTNAME = 384
    DOMEPORTNUMBER = 385
    METEODEVICENAME = 386
    METEOCONTROLLERNAME = 387
    METEOHOSTNAME = 388
    METEOPORTNUMBER = 389
    MOUNTDEVICENAME = 390
    MOUNTMODE = 391
    MOUNTCONTROLLERNAME = 392
    TMDEVICENAME = 393
    TMMODE = 394
    TMCONTROLLERNAME = 395
    TMINDEX = 396
    IMAGESCANFILTER = 397
    IMAGESEARCHRADIUS = 398
    IMAGESCALERANGE = 399
    IMAGEMINOBJECTS = 400
    IMAGECENTERRA2000 = 401
    IMAGECENTERDEC2000 = 402
    IMAGEORIENTATION = 403
    IMAGESCALE = 404
    IMAGEREFRA2000 = 405
    IMAGEREFDEC2000 = 406
    OBSPLAN = 407
    OBJECTNAME = 408
    NTARGETS = 409
    BASENAME = 410
    OBJECTTYPE = 411
    NMAXSERIES = 412
    NIMAGES = 413
    DTIMAGES = 414
    DTSERIES = 415
    NSERIES = 416
    BEGINVISIBILITY = 417
    ENDVISIBILITY = 418
    PRIORITY = 419
    FOCUSTYPE = 420
    EPHEMERISDATALENGTH = 421
    EPHEMERISDATATYPE = 422
    POSREF1 = 423
    POSREF2 = 424
    LEAPSECONDS = 425
    UT1UTC = 426
    STATUS = 427
    ROTATORNUMBER = 428
    SERIESDELAY = 429
    IMAGESCANSIGMA = 430
    DEROTATEIMAGE = 431
    MOTORDIR = 432
    POSITION3 = 433
    MODULENAME = 434
    PARKAZIMUTH = 435
    PARKELEVATION = 436
    PARKSIDEOFPIER = 437
    MAXTRACKINGERRORROTATOR = 438
    XDOME = 439
    YDOME = 440
    ZDOME = 441
    RDOME = 442
    DOMERADIUS = 443
    DOMEAZOFFSET = 444
    DOMEMAXAZDIFF = 445
    DOMEPARKAZ = 446
    SERVERNAME = 447
    TMPOSITION2 = 448
    TMPOSITION3 = 449
    TMPOSITION4 = 450
    OVERSCAN = 451
    XMINSENSOR = 452
    YMINSENSOR = 453
    SENSORWIDTH = 454
    SENSORHEIGHT = 455
    NAME = 456
    ABSOLUTEPOSITION = 457
    NEXTOBSTIME = 458
    DFOCPOS = 459
    T1 = 460
    T2 = 461
    FLUX = 462
    PQ = 463
    DIRECTORY = 464
    CURRENTD = 465
    CURRENTD1 = 466
    CURRENTD2 = 467
    FILTCF = 468
    FILTQ = 469
    MAXSPEED = 470
    MAXACCELERATION = 471
    NUMBEROFPOLEPAIRS = 472
    CURRENTLIMIT = 473
    CURRENTPEAKLIMIT = 474
    CURRENTPEAKTIME = 475
    CURRENTPHASELIMIT = 476
    CURRENTQLIMIT1 = 477
    CURRENTQLIMIT2 = 478
    MINPOS1 = 479
    MINPOS2 = 480
    MAXPOS1 = 481
    MAXPOS2 = 482
    STOPPERPOS1 = 483
    STOPPERPOS2 = 484
    NOHALLMEASTIME = 485
    NOHALLSTEPSIZE = 486
    NOHALLTESTCURRENT = 487
    WINDSPEED = 488
    UUID = 489
    ACCDEVICELIST = 490
    ACCTCPTIMEOUT = 491
    METEOTCPTIMEOUT = 492
    DOMETCPTIMEOUT = 493
    DOMESYNCAZIMUTH = 494

    MOUNTSLEWING = 495
    MOUNTTRACKING = 496
    MOUNTCONTROLLERDELAY = 497
    ILLUMINANCE = 498
    ANGLECONFIG = 499
    CAMERAORIENTATION = 500
    CAMERAISFIXED = 501

    FOCUSERNUMBER = 502
    FWNUMBER = 503
    FOCUSERUUID = 504
    FWUUID = 505
    ROTATORUUID = 506

    MINSUNDISTANCE = 507
    AUTOOPENCOVERS = 508
    DOMESHUTTERMODE = 509
    DOMEOPERATIONMODE = 510
    DOMESPEED = 511

    ACCDEBUGON = 512
    ACCLOGON = 513
    ACCLOGINTERVAL = 514
    CAMERADEBUGON = 515
    CAMERALOGON = 516
    CAMERALOGINTERVAL = 517

    INCLUDESUBDIRS = 518
    DOMEBLUETOOTHCONNECTED = 519
    TMSLEWINGOFFSET = 520
    MAXADU = 521
    OBJECTPHI = 522
    DOMEUSESPEEDMODE = 523
    DOMESPEEDMODELIMIT = 524

    GPSFIX = 525
    GPSFIXTYPE = 526
    NGPS = 527
    GPSISINSTALLED = 528
    GGAFRAME = 529
    GSAFRAME = 530

    NUMBEROFBLADES = 531
    CURRENTQLIMIT = 532
    STOPPERPOSITION = 533
    TLEFILENAME = 534
    TMSLEWING = 535

    CAMERAREADOUTSPEEDS = 536
    CAMERAGAINS = 537
    CALCULVISIBILITY = 538
    MAXHOURS = 539
    USEPVTSTAR = 540
    USEPVTSATELLITE = 541
    IMAGEISMIRRORED = 542
    POINTINGFILENAMEWEST = 543
    POINTINGFILENAMEEAST = 544
    POINTING_FLEXSQUARE = 545
    RMS_POINTING_FLEXSQUARE = 546
    OBSPLANID = 547
    NEXTOBJECTNAME = 548
    NEXTEXPTIME = 549
    NEXTFILPOS = 550
    NEXTDTIMAGES = 551
    NEXTNIMAGES = 552
    OBSAUTOSTART = 553
    CHECKCONNECTIONS = 554

    UNKNOWN = 555


class ParameterType(Enum):
    """Parameter type code"""
    DOUBLE = 48
    INT64 = 49
    INT32 = 50
    INT16 = 51
    CHAR = 52
    PCHAR = 53
    PBYTE = 54


class CommandStatus(Enum):
    """Command status code"""
    DONE = 32
    STARTED = 33
    MOVING = 34
    CMDERROR = 35
    REQUEST = 36
    INPROGRESS = 37


class EquatorialCoordinateType(Enum):
    # Custom or unknown equinox and/or reference frame.
    UNKNOWN = 0
    # Local topocentric; this is the most common for amateur telescopes.
    LOCALTOPOCENTRIC = 1
    # J2000 equator/equinox, ICRS reference frame.
    J2000 = 2
    # J2050 equator/equinox, ICRS reference frame. NOT supported!
    J2050 = 3
    # B1950 equinox, FK4 reference frame. NOT supported!
    B1950 = 4


class GuideDirections(Enum):
    """The direction in which the guide-rate motion is to be made."""
    # North (+ declination/altitude).
    guideNorth = 0
    # South (- declination/altitude).
    guideSouth = 1
    # East (+ right ascension/azimuth).
    guideEast = 2
    # West (- right ascension/azimuth)
    guideWest = 3


class DDM500(object):
    """ASA mount control class using TCP/IP commands.

    This class is based on the ASASDK C++ package.

    Parameters
    ----------
    address : str
        Mount server IP
    port : int
        Mount server port

    log : logger, optional
        logger to log to
        default = None
    log_debug : bool, optional
        log debug strings?
        default = False
    """

    def __init__(self, address, port, log=None, log_debug=False):
        self.address = address
        self.port = port
        self.buffer_size = 1024

        self._status_update_time = 0

        # Create a logger if one isn't given
        if log is None:
            logging.basicConfig(level=logging.INFO)
            log = logging.getLogger('mount')
            log.setLevel(level=logging.DEBUG)
        self.log = log
        self.log_debug = log_debug

        # Create one persistent socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.socket.connect((self.address, self.port))
        self.thread_lock = threading.Lock()
        self.connect()

        # Update status when starting
        self._update_status()

    def __del__(self):
        try:
            self.disconnect()
            self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()
        except OSError:
            pass

    def _tcp_command(self, command_str):
        """Send a string to the device, then fetch the reply and return it as a string."""
        try:
            if self.log and self.log_debug:
                self.log.debug('SEND:"{}"'.format(command_str))
            with self.thread_lock:
                self.socket.send(command_str.encode())
                reply = self.socket.recv(self.buffer_size).decode()
            if self.log and self.log_debug:
                self.log.debug('RECV:"{}"'.format(reply))
            return reply
        except Exception:
            self.log.error('Failed to communicate with mount')
            self.log.debug('', exc_info=True)
            raise

    def _format_tcp_command(self, command, params):
        """Correctly format a TCP command for the ASA mount."""
        # RecordStartchar
        msg = '$SOR,'

        # RecordWidth (fill at end)
        msg += '{:0>10.0f},'

        # Command
        command_num = Commands[command].value
        msg += str(command_num) + ','

        # Nparams
        msg += str(len(params)) + ','

        # Parameters
        for param in params:
            keyword_num = Keywords[param[0]].value
            value_type_num = ParameterType[param[1]].value
            value = param[2]
            msg += '{}={}:{},'.format(keyword_num, value_type_num, value)

        # MessageTerminator
        msg += '$EOM,'

        # RecordTerminator
        msg += '$EOR'

        # (fill in RecordWidth)
        msg = msg.format(len(msg))

        return msg

    def _parse_tcp_reply(self, reply_str):
        """Parse the return string from an ASA command."""
        # self._reply_string = reply_str
        reply = reply_str.split(',')

        # check format matches what we expect
        if reply[0] != '$SOR' or reply[-1] != '$EOR':
            raise ValueError('Invalid return string: {}'.format(reply_str))

        # find command and params
        command_num = int(reply[2])
        command = Commands(command_num).name

        nparams = int(reply[3])
        param_dict = {}
        for i in range(nparams):
            param = reply[4 + i].replace('=', ':').split(':')
            keyword = Keywords(int(param[0])).name
            value_type = ParameterType(int(param[1])).name
            value = param[2]

            # deal with types
            try:
                if value_type == 'DOUBLE':
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                value = str(value)

            # special values
            if keyword == 'CMDSTATUS':
                value = CommandStatus(value).name

            # add to dict
            param_dict[keyword] = value

        return command, param_dict

    def _command(self, command, params):
        """Send a command to the mount and parse the reply."""
        command_str = self._format_tcp_command(command, params)
        reply_str = self._tcp_command(command_str)
        reply_command, reply_params = self._parse_tcp_reply(reply_str)
        if reply_command != command:
            raise ValueError('Reply command {} does not match sent command {}'.format(
                             reply_command, command))
        if 'CMDSTATUS' not in reply_params:
            raise ValueError('Reply does not include status: {}'.format(reply_str))
        if reply_params['CMDSTATUS'] == 'CMDERROR' and 'ERRORMSG' in reply_params:
            raise ValueError(reply_params['ERRORMSG'])
        return reply_params

    def _send_command(self, command, params=None):
        """Send a command to the mount."""
        param = ('CMDSTATUS', 'CHAR', CommandStatus['REQUEST'].value)
        if params is None:
            params = [param]
        else:
            params = [param] + params

        reply = self._command(command, params)

        return reply

    def _get_property(self, property):
        """Get a keyword property from the mount."""
        command = 'GETPROPERTY'
        param = ('PROPERTY', 'INT16', Keywords[property].value)

        reply = self._command(command, [param])

        if property not in reply:
            raise ValueError('Property {} not in reply {}'.format(property, reply))
        return reply[property]

    def _set_property(self, property, value):
        """Set the value of a keyword property of the mount."""
        command = 'SETPROPERTY'
        if isinstance(value, str):
            param_type = 'CHAR'
        elif isinstance(value, int):
            param_type = 'INT16'
        else:
            param_type = 'DOUBLE'
        params = [('PROPERTY', 'INT16', Keywords[property].value),
                  (property, param_type, value)]

        reply = self._command(command, params)

        return reply

    def connect(self):
        """Connect to the mount device."""
        reply = self._send_command('TELCONNECT')
        return reply['CMDSTATUS']

    @property
    def connected(self):
        """Check connection to the mount device."""
        reply = self._send_command('TELCONNECTED')
        if 'CONNECTED' not in reply:
            raise ValueError('Unexpected reply: {}'.format(reply))
        return bool(reply['CONNECTED'])

    def disconnect(self):
        """Disconnect from the mount device."""
        reply = self._send_command('TELDISCONNECT')
        return reply['CMDSTATUS']

    def _update_status(self):
        """Read and store status values."""
        # Only update if we need to, to save sending multiple commands
        if (time.time() - self._status_update_time) > 0.5:
            # Get main status
            params = [('TELTIME', 'DOUBLE', -1.0)]
            status_dict = self._send_command('MOUNTSTATUS', params)
            self._jd = float(status_dict['UTC'])
            self._ra_jnow = float(status_dict['RIGHTASCENSION'])
            self._dec_jnow = float(status_dict['DECLINATION'])
            # Need to "uncook" from apparent to J2000
            ra_j2000, dec_j2000 = apparent_to_j2000(self._ra_jnow * 360 / 24,
                                                    self._dec_jnow,
                                                    self._jd)
            self._ra = ra_j2000 * 24 / 360
            if self._ra >= 24:
                self._ra -= 24
            self._dec = dec_j2000
            self._az = float(status_dict['AZIMUTH'])
            self._alt = float(status_dict['ELEVATION'])
            self._slewing = bool(status_dict['TELSLEWING'])
            self._tracking = bool(status_dict['TELTRACKING'])
            self._initializing = bool(status_dict['INITIALIZING'])
            self._position_error = {'ra': float(status_dict['POSITIONERROR1']),
                                    'dec': float(status_dict['POSITIONERROR2'])}
            self._tracking_error = {'ra': float(status_dict['TRACKINGERROR1']),
                                    'dec': float(status_dict['TRACKINGERROR2'])}
            self._velocity = {'ra': float(status_dict['VELOCITY1']),
                              'dec': float(status_dict['VELOCITY2'])}
            self._acceleration = {'ra': float(status_dict['ACCELERATION1']),
                                  'dec': float(status_dict['ACCELERATION2'])}
            self._current = {'ra': float(status_dict['CURRENTQ1']),
                             'dec': float(status_dict['CURRENTQ2'])}

            # Get tracking rates and timestamps
            ra_status = self._send_command('RIGHTASCENSIONRATE')
            dec_status = self._send_command('DECLINATIONRATE')
            self._tracking_rate = {'ra': float(ra_status['RA_RATE']),
                                   'dec': float(dec_status['DEC_RATE'])}
            self._timestamps = {'ra': float(ra_status['TELTIME']),
                                'dec': float(dec_status['TELTIME'])}

            # Get other properties
            self._parked = bool(self._get_property('ATPARK'))

            # store update time
            self._status_update_time = time.time()

    @property
    def status(self):
        """Return the current mount status."""
        self._update_status()
        if not self.connected:
            status = 'CONNECTION ERROR'
        elif self._parked:
            status = 'Parked'
        elif self._slewing:
            status = 'Slewing'
        elif self._tracking:
            status = 'Tracking'
        else:
            status = 'Stopped'
        return status

    @property
    def tracking(self):
        """Return if the mount is currently tracking."""
        self._update_status()
        return self._tracking

    @property
    def nonsidereal(self):
        """Return if the mount has a non-sidereal tracking rate set."""
        return self.tracking_rate['ra'] != 0 or self.tracking_rate['dec'] != 0

    @property
    def slewing(self):
        """Return if the mount is currently slewing."""
        self._update_status()
        return self._slewing

    # @property
    # def parking(self):
    #     """Return if the mount is currently parking."""
    #     self._update_status()
    #     return self._parking

    @property
    def parked(self):
        """Return if the mount is currently parked."""
        self._update_status()
        return self._parked

    @property
    def ra(self):
        """Return the current pointing RA."""
        self._update_status()
        return self._ra

    @property
    def dec(self):
        """Return the current pointing Dec."""
        self._update_status()
        return self._dec

    @property
    def alt(self):
        """Return the current altitude."""
        self._update_status()
        return self._alt

    @property
    def az(self):
        """Return the current azimuth."""
        self._update_status()
        return self._az

    @property
    def position_error(self):
        """Return the current position error."""
        self._update_status()
        return self._position_error

    @property
    def tracking_error(self):
        """Return the current tracking error."""
        self._update_status()
        return self._tracking_error

    @property
    def motor_current(self):
        """Return the current motor current."""
        self._update_status()
        return self._current

    @property
    def tracking_rate(self):
        """Return the current tracking rate."""
        self._update_status()
        return self._tracking_rate

    def slew_to_radec(self, ra, dec, ra_rate=None, dec_rate=None, set_target=True):
        """Slew to given RA and Dec coordinates (J2000), and set tracking rate (arcseconds/sec)."""
        if set_target:
            self.target_radec = (ra, dec)

        # first need to "cook" the coordinates into apparent
        ra_jnow, dec_jnow = j2000_to_apparent(ra * 360 / 24, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        if self.log and self.log_debug:
            self.log.debug('Cooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                ra, dec, ra_jnow, dec_jnow))

        if ra_rate is None:
            ra_rate = 0
        if dec_rate is None:
            dec_rate = 0

        params = [('TIMETAG', 'DOUBLE', -1),
                  ('DECLINATION', 'DOUBLE', float(dec_jnow)),
                  ('RIGHTASCENSION', 'DOUBLE', float(ra_jnow)),
                  ('DEC_RATE', 'DOUBLE', float(dec_rate)),
                  ('RA_RATE', 'DOUBLE', float(ra_rate)),
                  ('REFSYSTEM', 'INT16', EquatorialCoordinateType['LOCALTOPOCENTRIC'].value),
                  ('PIERSIDE', 'INT16', -1),  # select automatically
                  ]
        reply = self._send_command('MOUNTSLEWTOSTARASYNC', params)
        return reply['CMDSTATUS']

    def slew_to_altaz(self, alt, az, set_target=True):
        """Slew mount to given Alt/Az."""
        if set_target:
            self.target_altaz = (alt, az)

        params = [('AZIMUTH', 'DOUBLE', float(az)),
                  ('ELEVATION', 'DOUBLE', float(alt)),
                  ('PIERSIDE', 'INT16', -1),
                  ]
        reply = self._send_command('MOUNTSLEWTOAZELEASYNC', params)
        return reply['CMDSTATUS']

    def sync_radec(self, ra, dec):
        """Set current pointing to given RA and Dec coordinates (in J2000)."""
        # first need to "cook" the coordinates into apparent
        ra_jnow, dec_jnow = j2000_to_apparent(ra * 360 / 24, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        if self.log and self.log_debug:
            self.log.debug('Cooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                ra, dec, ra_jnow, dec_jnow))

        params = [('RIGHTASCENSION', 'DOUBLE', float(ra)),
                  ('DECLINATION', 'DOUBLE', float(dec)),
                  ]
        reply = self._send_command('SYNCTOCOORDINATES', params)
        return reply['CMDSTATUS']

    def sync_altaz(self, alt, az):
        """Set current pointing to given Alt/Az."""
        params = [('AZIMUTH', 'DOUBLE', float(az)),
                  ('ELEVATION', 'DOUBLE', float(alt)),
                  ]
        reply = self._send_command('SYNCTOALTAZ', params)
        return reply['CMDSTATUS']

    def track(self):
        """Start tracking at the siderial rate."""
        reply = self._set_property('TELTRACKING', '1')
        return reply['CMDSTATUS']

    def park(self):
        """Move mount to park position."""
        reply = self._send_command('PARK')
        return reply['CMDSTATUS']

    def unpark(self):
        """Unpark the mount so it can accept slew commands."""
        reply = self._send_command('UNPARK')
        return reply['CMDSTATUS']

    def halt(self):
        """Abort slew (if slewing) and stop tracking (if tracking)."""
        reply = self._send_command('ABORTSLEW')
        return reply['CMDSTATUS']

    def offset(self, direction, distance):
        """Set offset in the given direction by the given distance (in arcsec)."""
        if direction.upper() not in ['N', 'E', 'S', 'W']:
            raise ValueError('Invalid direction "{}" (should be [N,E,S,W])'.format(direction))
        if not self.tracking:
            raise ValueError('Can only offset when tracking')
        angle = {'N': 0, 'E': 90, 'S': 180, 'W': 270}
        old_coord = SkyCoord(self.ra * u.hourangle, self.dec * u.deg)
        new_coord = old_coord.directional_offset_by(angle[direction] * u.deg, distance * u.arcsec)
        self.slew_to_radec(new_coord.ra.hourangle, new_coord.dec.deg, set_target=False)

    def error_check(self):
        """Check for any errors logged by the mount."""
        error_status = self._send_command('ERRORRAISED')
        if bool(error_status['ERRORRAISED']):
            raise ValueError(error_status['ERRORMSG'])
