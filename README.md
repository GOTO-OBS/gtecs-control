# G-TeCS

**G-TeCS** (*gee-teks*) is the **Te**lescope **C**ontrol **S**ystem for the **G**OTO observatory, based on the system developed by Durham and Sheffield for the pt5m robotic telescope (https://sites.google.com/site/point5metre/). 

Each piece of hardware (mount, camera, dome etc) gets its own daemon, which issues controls to the hardware. Each daemon contains a control thread which loops continuously when running to check for commands. When a command is sent an internal flag will be set, which will be caught by the control thread and the relevant command will be issued to the hardware. Under this system every command is instantaneous and designed to return immediately, meaning it can always be interrupted in case of emergency. Each hardware daemon (e.g. *cam_daemon.py*) currently has its own control script (e.g. *cam.py*), which is used to start and shutdown the daemon and issue commands to it via the terminal. 

The scripts and daemons communicate using PyRO (Python Remote Objects) commands (https://pythonhosted.org/Pyro4/), a very flexible system that allows any combination of daemons to be controlled from one command script (as is curently done with the queue daemon) and commands to be issued to different computers over the network (even on different operating systems, such as with the sitech daemon).

At the moment there are 5 daemons:
* *mnt* (or mount) - to control the SiTech mount (called *tel* for pt5m)
 * *sitech* - a sub-daemon on a Windows PC that converts mount commands to ASCOM functions (not included)
* *cam* - to control the FLI camera (called *ccd* for SLODAR and *qsi* for pt5m)
* *foc* - to control the FLI focuser attached to the camera
* *filt* - to control the FLI filter wheel attached to the camera
* *queue* - to control the image queue and issue orders to the FLI hardware daemons

There will be (at least) 2 more in the final system:
* *dome* - to control the dome
* *power* - to control powering the hardware

There are also two helpful scripts:
* *lilith.py* - used to issue common commands (start, ping, shutdown, kill) to multiple daemons
* *follower.py* - used to monitor daemon statuses by constantly calling their info commands every second

Finally there are three common module files:
* *X_logger.py* - contains a standard class for daemons outputing log files
* *X_misc.py* - contains common functions, such as starting and shutting down daemons and astronomical conversions
* *X_params.py* - contains common daemon and system parameters, including file paths and hardware parameters

At the moment all the above files and scripts are just dumped in the main directory.

The daemons will be controlled by an overall script called the pilot, which sends orders to each daemon and is in charge of monitoring the weather via the conditions monitor, as well as startup and shutdown at the beginning and end of the night. This has not yet been developed for G-TeCS.
