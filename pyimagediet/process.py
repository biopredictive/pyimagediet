import copy
import filecmp
import magic
import os
from os.path import exists, isfile
import shutil
from subprocess import call, PIPE
import yaml


class DietException(Exception):
    def __init__(self, msg='', *args):
        self.msg = msg


class NotFileDietException(DietException): pass
class CompressFileDietException(DietException): pass
class ConfigurationErrorDietException(DietException): pass


def read_yaml_configuration(filename):
    '''Parse configuration in YAML format into a Python dict'''
    with open(filename, 'r') as f:
        config = yaml.load(f.read())
    return config


def determine_type(filename):
    '''Determine the file type and return it.'''
    ftype = magic.from_file(filename, mime=True).decode('utf8')
    if ftype == 'text/plain':
        ftype = 'text'
    elif ftype == 'image/svg+xml':
        ftype = 'svg'
    else:
        ftype = ftype.split('/')[1]
    return ftype


def parse_configuration(config):
    '''
    Parse and fix configuration:
        - processed file should end up being same as input
        - pipelines should contain CLI commands to run
    '''
    new_config = copy.deepcopy(config)

    # Always end up with input file. If app outputs to a different one,
    # then replace old one with it
    for prog in new_config['parameters']:
        if '{output_file}' in new_config['parameters'][prog]:
            new_config['parameters'][prog] = new_config['parameters'][prog] + " && mv '{output_file}' '{file}'"

    # Build pipelines
    for label, raw_pipeline in new_config['pipelines'].items():
        commands = []
        for app in raw_pipeline:
            full_command = " ".join([new_config['commands'][app],
                                     new_config['parameters'][app]])
            commands.append(full_command)
        new_config['pipelines'][label] = " && ".join(commands)

    return new_config


def update_configuration(orig, new):
    '''Update existing configuration with new values. Needed because
    dict.update is shallow and would overwrite nested dicts.

    Function updates sections commands, parameters and pipelines and adds any
    new items listed in updates.
    '''
    dicts = ('commands', 'parameters', 'pipelines')
    for key in dicts:
        if key in new:
            orig[key].update(new[key])

    for key in new:
        if key not in dicts:
            orig[key] = new[key]




def check_configuration(config):
    sections = ('commands', 'parameters', 'pipelines')
    # Check all sections are there and contain dicts
    for section in sections:
        if section not in config:
            error_msg = 'Error: Section {0} is missing.'.format(section)
            raise ConfigurationErrorDietException(error_msg)
        if not isinstance(config[section], dict):
            error_msg = 'Error: Section {0} is malformed.'.format(section)
            raise ConfigurationErrorDietException(error_msg)

    # Check every command has a corresponding parameters entry
    commands_cmds = set(list(config['commands'].keys()))
    parameters_cmds = set(list(config['parameters'].keys()))
    if commands_cmds != parameters_cmds:
        error_msg = ('Every command in commands and parameters section has to '
                     'have a corresponding entry in the other section.')
        raise ConfigurationErrorDietException(error_msg)

    # Check pipelines section contains lists as values and each of them only
    # has entries listed in commands section
    for cmd in config['pipelines']:
        pipeline = config['pipelines'][cmd]
        if not isinstance(pipeline, list):
            error_msg = ('Error: Pipeline {0} is malformed. Values have to '
                         'be a list of command names.').format(cmd)
            raise ConfigurationErrorDietException(error_msg)
        for tool in pipeline:
            if tool not in commands_cmds:
                error_msg = ('Error in pipeline {0}. "{1}" cannot be found '
                             'among commands listed in commands '
                             'section').format(cmd, tool)
                raise ConfigurationErrorDietException(error_msg)



def backup_file(filename, backup_ext):
    '''
    Make a file backup if:
    - backup extension is defined
    - backup file does not exist yet
    If backup file exists:
    - do nothing if it is same
    - complain loudly otherwise

    Return file name of the back up if successful. None otherwise.
    '''
    ext = backup_ext.strip(" .")
    if ext:
        backup_filename = ".".join([filename, ext])
        if not exists(backup_filename):
            shutil.copyfile(filename, backup_filename)
        else:
            if not filecmp.cmp(filename, backup_filename):
                raise DietException('Cannot make backup because a different'
                                    'file with that name already exists.')
        return backup_filename
    return None


def copy_if_different(src, dst):
	if not filecmp.cmp(src, dst):
		shutil.copyfile(src, dst)


def squeeze(cmd, filename, backup_filename):
    # Some tools save result in a separate file so this is used as an
    # intermediate result.
    tmpfile = ".".join([filename, "diet_tmp"])

    retcode = call(cmd.format(file=filename, output_file=tmpfile),
                   shell=True, stdout=PIPE)
    if retcode != 0:
        # Failed; Likely missing some utilities
        copy_if_different(backup_filename, filename)
        raise CompressFileDietException(
            "Squeezing failed. Likely because of missing required utilities.")
    return os.stat(filename).st_size


def diet(filename, configuration):
    '''
    Squeeze files if there is a pipeline defined for them or leave them be
    otherwise.

    Makes a backup of a file, but only if file will be processed.

    Return new size of the file in bytes.
    '''
    changed = False

    if not isfile(filename):
        raise NotFileDietException('Passed filename does not point to a file')
    # Wasteful to do parsing every time, but does not really matter as it takes
    # practically nothing in comparison with compression
    conf = parse_configuration(configuration)

    filetype = determine_type(filename)
    squeeze_cmd = conf['pipelines'].get(filetype)
    if squeeze_cmd:
        tmpbackup_ext = 'diet_internal'
        ext = conf.get('backup', tmpbackup_ext)
        backup = backup_file(filename, ext)

        size = os.stat(filename).st_size
        new_size = squeeze(squeeze_cmd, filename, backup)

        if not conf.get('keep_processed', False) and new_size > size:
            copy_if_different(backup, filename)

        # Delete backup, if it was internal
        if not conf.get('backup'):
            os.remove(backup)
        changed = True

    return changed
