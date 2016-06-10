# G-TeCS

**G-TeCS** (*gee-teks*) is the **Te**lescope **C**ontrol **S**ystem for the **G**OTO observatory, based on the system developed by Durham and Sheffield for the pt5m robotic telescope (https://sites.google.com/site/point5metre/). 

Each piece of hardware making up the telescope (mount, camera, dome etc) gets its own daemon, through which commands are sent to the hardware. When running each daemon contains a control thread which loops continuously in order to catch any incoming commands. When a command is sent to the daemon an internal flag is set, this is caught by the control thread and the relevant command will be issued to the hardware. Under this system every command is instantaneous and designed to return immediately, meaning it can always be interrupted in case of emergency. Each hardware daemon (e.g. *cam_daemon.py*) currently has its own control script (e.g. *cam.py*), which is used to start and shutdown the daemon and issue commands to it via the terminal. 

The scripts and daemons communicate using PyRO (Python Remote Objects) commands (https://pythonhosted.org/Pyro4/), a very flexible system for calling objects and functions from other Python scripts. A daemon script is made into a 'server' which publishes its commands and objects for any script on the network to use. It allows daemon functions to be controlled from multiple command scripts and for commands to be issued to different computers over the network (via the "interface" daemons), even on different operating systems (as with the SiTech interface).

At the moment there are 7 primary daemons:
* *cam* - to control the FLI camera (called *ccd* for SLODAR and *qsi* for pt5m)
* *filt* - to control the FLI filter wheel attached to the camera (previously built into *qsi*)
 * *exq* (queue) - to control the exposure queue, sends orders to the above two daemons (previously part of *qsi*)
* *foc* - to control the FLI focuser attached to the camera
* *mnt* (mount) - to control the SiTech mount (called *tel* for pt5m)
* *dome* - to control the AstroHaven dome
* *power* - to control the APC power supply

The *cam*, *foc* and *filt* daemons are designated *meta-daemons*. Unlike the other hardware daemons (or their own first iterations), meta-daemons control more than one set of their associated hardware. The planned GOTO setup initially includes 4 unit telescopes (expanding to 8 later); thus 4 sperate cameras, 4 focusers and 4 filter wheels. Running multiple coppies of the daemon scripts would be inefficient, so the meta-daemons are built to issue commands to multiple cameras/focusers/filter wheels in parallel when required.

Often the hardware will not be physically connected to the computer the daemons are running on, but another on the same network. This is easy to do over a PyRO connection, and allows the intermediary *interface* script to convert the commands from Python functions to whichever format the hardware uses. 
* The FLI daemons (*cam*, *foc* and *filt*) send orders to the FLI hardware via an *fil_interface* daemon running on the computer the hardware is connected to. This is required due to the design of GOTO not allowing all the hardware on the mount to be connected directly via USB to the control computer.
* The mount daemon (*mnt*) sends its commands to the SiTech mount controller via the *sitech_interface* daemon running on the mount control computer. This is required as the SiTech mount accepts ASCOM commands, which only works under Windows. So the intermediate mount control computer is a Windows PC, which can still connect to the PyRO network and convert any commands to their ASCOM equivalents.

There are also two helpful scripts:
* *lilith.py* - used to issue common commands (start, ping, shutdown, kill) to multiple daemons
* *follower.py* - used to monitor daemon statuses by constantly calling their info commands every second

The *tecs_modules* folder contains several common modules and control classes:
* *misc.py* - contains common functions, such as starting and shutting down daemons and astronomical conversions
* *params.py* - contains common daemon and system parameters, including file paths and hardware parameters
* *logger.py* - contains a standard class for daemons outputting log files
* *flags.py* - contains standard classes to read external flag files
* *dome_control.py* - contains the dome control classes used by the dome daemon
* *power_control.py* - contains the power control classes used by the power daemon

The daemons will be controlled by an overall script called the pilot, which sends orders to each daemon and is in charge of monitoring the weather via the conditions monitor, as well as startup and shutdown at the beginning and end of the night. This has not yet been developed for G-TeCS.

Martin Dyer
Last update: 26 Feb 2016
