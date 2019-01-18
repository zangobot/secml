"""
.. module:: Settings
   :synopsis: System settings for PraLib

.. moduleauthor:: Marco Melis <marco.melis@diee.unica.it>

"""
import os
from ConfigParser import SafeConfigParser, NoSectionError, NoOptionError

from secml.utils import fm, CLog


__all__ = ['SECML_HOME_DIR', 'SECML_CONFIG',
           'SECML_EXP_DIR', 'SECML_DS_DIR']


def parse_config(conf_files, section, parameter, default=None, dtype=None):
    """Parse input `parameter` under `section` from configuration files.

    Parameters file must have the following structure:

        [section1]
        param1=xxx
        param2=xxx

        [section2]
        param1=xxx
        param2=xxx

    Parameters
    ----------
    conf_files : list
        List with the paths of the configuration files to parse.
    section : str
        Section under which look for specified parameter.
    parameter : str
        Name of the parameter. This is not case-sensitive.
    default : any
        Set default value of parameter.
        If None (default), parameter is considered required and
        so must be defined in the input configuration file.
        If not None, the value will be used if configuration file
        does not exists, section is not defined, or the parameter
        is not defined under section.
    dtype : type or None, optional
        Expected dtype of the parameter.
        If None (default), parameter will be parsed as a string.
        Other accepted values are: float, int, bool, str.

    """
    # Parsing parameters
    _config = SafeConfigParser()

    # Parse configuration files (even if not exists)
    # The first item of the list has LOWER priority (so we reverse)
    _config.read(reversed(conf_files))

    # Try to parse the parameter from section
    try:
        # Call the get function appropriate to specified dtype
        if dtype is None or dtype == str:
            param = _config.get(section, parameter)
        elif dtype == int:
            param = _config.getint(section, parameter)
        elif dtype == float:
            param = _config.getfloat(section, parameter)
        elif dtype == bool:
            param = _config.getboolean(section, parameter)
        else:
            raise TypeError(
                "accepted dtypes are int, float, bool, str (or None)")
    except NoSectionError:
        if default is not None:
            # Use default if config file does not exists
            # or does not have the desired section
            return default
        raise RuntimeError("check that section `[{:}]` exists in {:}"
                           "".format(section, conf_files))
    except NoOptionError:
        if default is not None:
            # Use default if desired parameter is not specified under section
            return default
        raise RuntimeError("parameter `{:}` not found under section `[{:}]` "
                           "of {:}".format(parameter, section, conf_files))

    return param


def _parse_env(name, default=None, dtype=None):
    """Parse input variable from `os.environ`.

    Parameters
    ----------
    name : str
        Name of the variable to parse from env.
    default : any
        Set default value of variable.
        If None (default), parameter is considered required and
        so must be defined in environment.
        Otherwise, RuntimeError will be raised.
    dtype : type or None, optional
        Expected dtype of the variable.
        If None (default), variable will be parsed as a string.
        Other accepted values are: float, int, bool, str.

    """
    try:
        val = os.environ[name]
    except KeyError:
        if default is not None:
            # Let's use the default value if var not in env
            return default
        raise RuntimeError("variable {:} not specified".format(name))

    # Parse var from env using the specified dtype
    if dtype is None or dtype == str:
        return str(val)
    if dtype == int or dtype == float or dtype == bool:
        return dtype(val)
    else:
        raise TypeError(
            "accepted dtypes are int, float, bool, str (or None)")


def _parse_env_config(name, conf_files, section, parameter,
                      default=None, dtype=None):
    """Parse input variable from `os.environ` or configuration files.

    If input variable `name` is not found in `os.environ`,
    the corresponding parameter is parsed from configuration files.
    If not found, default variable value will be returned.
    If no default value has been defined, RuntimeError will be raised.

    Parameters
    ----------
    name : str
        Name of the variable to parse from env.

    For description of other input parameters see `.parse_config`.

    """
    try:  # Firstly let's try to get variable from environment
        # Don't pass default to _parse_env as we want
        # to catch KeyError and RuntimeError
        return _parse_env(name, default=None, dtype=dtype)
    except (KeyError, RuntimeError):
        # Probably the variable is not in env, try read config
        return parse_config(conf_files, section, parameter, default, dtype)


"""Main directory for storing datasets, experiments, temporary files.

This is set by default to:
    * Unix -> '$HOME/secml-lib-data'
    * Windows -> ($HOME, $USERPROFILE, $HOMEPATH, $HOMEDRIVE)/secml-lib-data'

"""
SECML_HOME_DIR = _parse_env('SECML_HOME_DIR',
                            default=os.path.join(os.path.expanduser('~'),
                                                 'secml-lib-data'))
if not fm.folder_exist(SECML_HOME_DIR):
    # Creating the home directory if not already available
    fm.make_folder(SECML_HOME_DIR)
    CLog(level='INFO', logger_id=__name__).info(
        'New `SECML_HOME_DIR` created: {:}'.format(SECML_HOME_DIR))


"""Name of the configuration file (default `secml-lib.conf`)."""
SECML_CONFIG_FNAME = 'secml-lib.conf'


def _config_fpath():
    """Returns the path of the active configuration file(s).

    The list of active configuration files is sorted from the highest
    to the lowest priority, as follows:
     - `$PWD/secml-lib.conf`
     - `$SECML_CONFIG` if it is not a directory
     - `$SECML_CONFIG/secml-lib.conf`
     - `$SECML_HOME_DIR/secml-lib.conf`
        - On Unix, `$HOME/secml-lib-data/secml-lib.conf`
        - On Windows, `($HOME, $USERPROFILE, $HOMEPATH, $HOMEDRIVE)/secml-lib-data/secml-lib.conf`
     - Lastly, it looks in `INSTALL/secml-lib/secml-lib.conf` for a
       system-defined copy.
       INSTALL is something like /usr/lib/python3.5/site-packages on Linux,
       and maybe C:\Python35\Lib\site-packages on Windows.

    Returns
    -------
    list
        The list of active configuration files is sorted from the highest
        to the lowest priority.

    """
    def gen_candidates():
        yield fm.join(os.getcwd(), SECML_CONFIG_FNAME)
        try:
            secml_config = os.environ['$SECML_CONFIG']
        except KeyError:
            pass
        else:
            yield secml_config
            yield fm.join(secml_config, 'SECML_CONFIG_FNAME')
        yield fm.join(SECML_HOME_DIR, SECML_CONFIG_FNAME)
        yield fm.normpath(
            fm.join(fm.abspath(__file__), '..', SECML_CONFIG_FNAME))

    candidates = []
    for fname in gen_candidates():
        if fm.file_exist(fname):
            candidates.append(fname)

    return candidates


"""Active configuration files `secml-lib.conf`."""
SECML_CONFIG = _config_fpath()


# ----------- #
# [SECML-LIB] #
# ----------- #

"""Main directory for storing datasets, subdirectory of SECML_HOME_DIR.

This is set by default to: 'SECML_HOME_DIR/datasets'

"""
SECML_DS_DIR = _parse_env_config(
    'SECML_DS_DIR', SECML_CONFIG, 'secml-lib', 'ds_dir',
    dtype=str, default=os.path.join(SECML_HOME_DIR, 'datasets')
)

"""Main directory of experiments data, subdirectory of SECML_HOME_DIR.

This is set by default to: 'SECML_HOME_DIR/experiments'

"""
SECML_EXP_DIR = _parse_env_config(
    'SECML_EXP_DIR', SECML_CONFIG, 'secml-lib', 'exp_dir',
    dtype=str, default=os.path.join(SECML_HOME_DIR, 'experiments')
)
