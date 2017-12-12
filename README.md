# G-TeCS

**G-TeCS** (*gee-teks*) is the **Te**lescope **C**ontrol **S**ystem for the **G**OTO observatory, written in Python3 and based on the system developed by Durham and Sheffield for the pt5m robotic telescope (https://sites.google.com/site/point5metre/).

Each piece of hardware making up the telescope (mount, camera, dome etc) gets its own *daemon*, through which commands are sent to the hardware. There are also several support daemons that monitor things like the external conditions and observation database. Often the hardware will not be physically connected to the computer the daemons are running on, but another on the same network. This is easy to do over a Pyro connection, and allows the intermediary *interface* script to convert the commands from Python functions to whichever format the hardware uses.

The overall system is controlled by the *pilot*. This is a script that is run every night, either automatically by a cronjob or by the observer. The pilot will do everything a human observer would in a night - power on, take calibration frames, work through the pointing database and then power off again in the morning. For standard operations (taking flats, autofocusing, end of night shutdown) the pilot uses a series of *observation scripts* to send commands to the daemons, these could also be run manually if desired.


Daemons
-------

When running each daemon contains a control thread which loops continuously in order to catch any incoming commands. When a command is sent to the daemon an internal flag is set, this is caught by the control thread and the relevant command will be issued to the hardware. Under this system every command is instantaneous and designed to return immediately, meaning it can always be interrupted in case of emergency. Each hardware daemon has its own control script (e.g. *cam*), which is used to start and shutdown the daemon and issue commands to it via the terminal.

The scripts and daemons communicate using Pyro (Python Remote Objects) commands (https://pythonhosted.org/Pyro4/), a very flexible system for calling objects and functions from other Python scripts. A daemon script is made into a 'server' which publishes its commands and objects for any script on the network to use. It allows daemon functions to be controlled from multiple command scripts and for commands to be issued to different computers over the network (via the "interface" daemons).


User manual
-----------
For infomation on running the system for GOTO, see the GitHub wiki.


Configuration
-------------
Configuration of **G-TeCS** is acheived using a config file, and the Python module configobj (http://configobj.readthedocs.io/en/latest/).
An example config file is present in the *data* directory of this repository. When running,
**G-TeCS** will look for a file named *.gtecs.conf* either in the current directory, the users home directory or any path
specified by the *GTECS_CONF* environment variable.

If no such file is available, **G-TeCS** will use the default config, as shown in the *data* directory. Users can over-ride
as many of these default settings as they wish in the *.gtecs.conf* file. Particular attention should be paid to the
*CONFIG_PATH* setting. This is where **G-TeCS** will save persistent files, image files and log files. A script ```setup_gtecs```
is provided to create this directory structure, and populate with the necessary files. It should be run after installation, and
after an appropriate value for the *CONFIG_PATH* is set in the *.gtecs.conf* file.


Martin Dyer
Last update: 12 Dec 2017
