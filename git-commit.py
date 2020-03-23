#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import os
import re
import stat
import tempfile
from distutils.version import LooseVersion

from ansible.module_utils._text import to_native, to_text
from ansible.module_utils.basic import AnsibleModule
from six import b

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'core'}

DOCUMENTATION = '''
---
module: git_cp
author:
    - "Federico Olivieri"
version_added: "2.10"
short_description: Perform git commiit and/or git push perations.
description:
    - Manage git commits and git push on local git directory
options:
    folder_path:
        description:
            - full folder path where .git/ is located.
        required: true
    user:
        description:
            - git username for https operations.
    token:
        description:
            - git API token for https operations.
    comment:
        description:
            - git commit comment. Same as "git commit -m".
    add:
        description:
            - list of files to be staged. Same as "git add ." 
              Asterisx values not accepted. i.e. "./*" or "*". 
        type: list
        default: "."
    branch:
        description:
            - git branch where perform git push.
        required: true
    push:
        description:
            - perform git push action. Same as "git push HEAD/branch".
        type: bool
        default: True
    commit:
        description:
            - git commit staged changes. Same as "git commit -m".
        type: bool
        default: True
    push_option:
        description:
            - git push options. Same as "git --push-option=option".
    mode:
        description:
            - git operations are performend eithr over ssh channel or https. 
              Same as "git@git..." or "https://user:token@git..."
        choices: [ 'ssh', 'https' ]
        default: ssh
        required: True
    url:
        description:
            - git repo URL. If not provided, the module will use the same mode used by "git clone"

requirements:
    - git>=2.19.0 (the command line tool)

notes:
    - "If the task seems to be hanging, first verify remote host is in known_hosts.
      SSH will prompt user to authorize the first contact with a remote host.  To avoid this prompt,
      one solution is to use the option accept_hostkey. Another solution is to
      add the remote host public key in C(/etc/ssh/ssh_known_hosts) before calling
      the git module, with the following command: ssh-keyscan -H remote_host.com >> /etc/ssh/ssh_known_hosts."
'''

EXAMPLES = '''

# Commit and push changes via https.
- git_cp:
    folder_path: /Users/federicoolivieri/git/git_test_module
    user: Federico87
    token: m1Ap!T0k3n!!!
    comment: My amazing backup
    add: ['test.txt', 'txt.test']
    branch: master
    push: true
    commit: true
    mode: https
    url: https://gitlab.com/networkAutomation/git_test_module           

# Push changes via ssh using some defaults.
- git_cp:
    folder_path: /Users/federicoolivieri/git/git_test_module
    comment: My amazing backup
    branch: master
    push: true
    commit: false
    url: git@gitlab.com/networkAutomation/git_test_module

# Commit and push changes using only defaults.
- git_cp:
    folder_path: /Users/federicoolivieri/git/git_test_module
    comment: My amazing backup
    branch: master
'''

RETURN = '''
to_do:
    lorem ipsum
'''


def get_module_path():
    return os.path.dirname(os.path.realpath(__file__))


def write_ssh_wrapper():
    module_dir = get_module_path()
    try:
        # make sure we have full permission to the module_dir, which
        # may not be the case if we're sudo'ing to a non-root user
        if os.access(module_dir, os.W_OK | os.R_OK | os.X_OK):
            fd, wrapper_path = tempfile.mkstemp(prefix=module_dir + '/')
        else:
            raise OSError
    except (IOError, OSError):
        fd, wrapper_path = tempfile.mkstemp()
    fh = os.fdopen(fd, 'w+b')
    template = b("""#!/bin/sh
if [ -z "$GIT_SSH_OPTS" ]; then
    BASEOPTS=""
else
    BASEOPTS=$GIT_SSH_OPTS
fi

# Let ssh fail rather than prompt
BASEOPTS="$BASEOPTS -o BatchMode=yes"

if [ -z "$GIT_KEY" ]; then
    ssh $BASEOPTS "$@"
else
    ssh -i "$GIT_KEY" -o IdentitiesOnly=yes $BASEOPTS "$@"
fi
""")
    fh.write(template)
    fh.close()
    st = os.stat(wrapper_path)
    os.chmod(wrapper_path, st.st_mode | stat.S_IEXEC)
    return wrapper_path


def set_git_ssh(ssh_wrapper, key_file, ssh_opts):
    if os.environ.get("GIT_SSH"):
        del os.environ["GIT_SSH"]
    os.environ["GIT_SSH"] = ssh_wrapper

    if os.environ.get("GIT_KEY"):
        del os.environ["GIT_KEY"]

    if key_file:
        os.environ["GIT_KEY"] = key_file

    if os.environ.get("GIT_SSH_OPTS"):
        del os.environ["GIT_SSH_OPTS"]

    if ssh_opts:
        os.environ["GIT_SSH_OPTS"] = ssh_opts


def get_repo_path(local_path):
    repo_path = os.path.join(local_path, '.git')
    # Check if the .git is a file. If it is a file, it means that the repository is in external
    # directory respective to the working copy (e.g. we are in a submodule structure).
    if os.path.isfile(repo_path):
        with open(repo_path, 'r') as gitfile:
            data = gitfile.read()
        ref_prefix, gitdir = data.rstrip().split('gitdir: ', 1)
        if ref_prefix:
            raise ValueError('.git file has invalid git dir reference format')

        # There is a possibility the .git file to have an absolute path.
        if os.path.isabs(gitdir):
            repo_path = gitdir
        else:
            repo_path = os.path.join(repo_path.split('.git')[0], gitdir)
        if not os.path.isdir(repo_path):
            raise ValueError('%s is not a directory' % repo_path)
    return repo_path


def git_version(git_path, module):
    """return the installed version of git"""
    cmd = "%s --version" % git_path
    (rc, out, err) = module.run_command(cmd)
    if rc != 0:
        # one could fail_json here, but the version info is not that important,
        # so let's try to fail only on actual git commands
        return None
    rematch = re.search('git version (.*)$', to_native(out))
    if not rematch:
        return None
    return LooseVersion(rematch.groups()[0])


def main():
    module = AnsibleModule(
        argument_spec=dict(
            local_path=dict(type='path', required=True),
            user=dict(),
            token=dict(),
            comment=dict(),
            add=dict(type="list", default=["."]),
            branch=dict(default="master", required=True),
            remote=dict(default="origin", required=True),
            push=dict(type="bool", default=True),
            set_upstream=dict(type="bool", default=False),
            commit=dict(type="bool", default=True),
            push_option=dict(),
            mode=dict(choices=["ssh", "https"], default="ssh", required=True),
            key_file=dict(default=None, type='path', required=False),
            ssh_opts=dict(default=None, required=False),
            executable=dict(default=None, type='path'),
        ),
        mutually_exclusive=[("ssh", "https")],
        required_if=[
            ("commit", True, ["comment", "add"]),
            ("mode", "https", ["user", "token"]),
            ("push", True, ["branch", "remote"]),
        ],
        supports_check_mode=True
    )

    local_path = module.params['local_path']
    user = module.params['user']
    token = module.params['token']
    comment = module.params['comment']
    add = module.params['add']
    branch = module.params['branch']
    remote = module.params['remote']
    push = module.params['push']
    set_upstream = module.params['set_upstream']
    commit = module.params['commit']
    push_option = module.params['push_option']
    mode = module.params['mode']
    key_file = module.params['key_file']
    ssh_opts = module.params['ssh_opts']
    git_path = module.params['executable'] or module.get_bin_path('git', True)

    if commit and not comment:
        module.fail_json(
            msg='Comment must be provided in order commit changes.'
        )

    result = {}

    if module.params['accept_hostkey']:
        if ssh_opts is not None:
            if "-o StrictHostKeyChecking=no" not in ssh_opts:
                ssh_opts += " -o StrictHostKeyChecking=no"
        else:
            ssh_opts = "-o StrictHostKeyChecking=no"

            # We screenscrape a huge amount of git commands so use C locale anytime we
            # call run_command()
            module.run_command_environ_update = dict(LANG='C', LC_ALL='C', LC_MESSAGES='C',
                                                     LC_CTYPE='C')

    gitconfig = None
    repo_path = os.path.realpath(local_path)
    try:
        repo_path = get_repo_path(local_path)
    except (IOError, ValueError) as err:
        # No repo path found
        """``.git`` file does not have a valid format for detached Git dir."""
        module.fail_json(
            msg='Current repo does not have a valid reference to a '
                'separate Git dir or it refers to the invalid path',
            details=to_text(err),
        )
    gitconfig = os.path.join(repo_path, 'config')

    # create a wrapper script and export
    # GIT_SSH=<path> as an environment variable
    # for git to use the wrapper script
    ssh_wrapper = write_ssh_wrapper()
    set_git_ssh(ssh_wrapper, key_file, ssh_opts)
    module.add_cleanup_file(path=ssh_wrapper)

    git_version_used = git_version(git_path, module)

    commands = list()

    if add:
        commands.append((
            'git_add',
            lambda *args, **kwargs: module.run_command(
                "{} -C {} add {}".format(
                    git_path,
                    repo_path,
                    ' '.join(add)
                ),
                cwd=local_path
            )
        ))
        commands.append((
            'git_files_added',
            lambda *args, **kwargs: module.run_command(
                "{} -C {} diff-index --cached --name-status HEAD".format(
                    git_path,
                    repo_path
                ),
                cwd=local_path
            )
        ))

        def _get_diff_index(*args, **kwargs):
            result.update({
                'local_path': local_path,
                'changed': False,
                'files': {}
            })

            staged_files = [*args[:-1].split('\n')]
            if len(staged_files) != 0:
                changes = {
                    'added': [],
                    'modified': [],
                    'deleted': []
                }
                for staged_file in staged_files:
                    [stage_action, filename] = re.split(r'\s+', str(staged_file))
                    if stage_action == 'A':
                        stage_action = 'added'
                    elif stage_action == 'D':
                        stage_action = 'deleted'
                    elif stage_action == 'M':
                        stage_action = 'modified'
                    else:
                        continue
                    changes[stage_action].append(filename)

                result['changed'] = True
                result['files'] = changes
                return 0, '', ''
            else:
                module.exit_json(**result)

        commands.append(('git_staged_files',
                         _get_diff_index))

    if commit:
        commands.append(
            ('git_commit',
             lambda *args, **kwargs: module.run_command(
                 '{} -C {} commit -m "{}"'.format(
                     git_path,
                     repo_path,
                     comment.replace("\"", r"\"", -1)
                 ),
                 cwd=local_path
             )
             ))

    if push:
        push_option = "--push-option={}".format(push_option) if push_option else ""
        commands.append(
            ('git_push',
             lambda *args, **kwargs: module.run_command(
                 '{} -C {} push {} {} {} {}'.format(
                     git_path,
                     repo_path,
                     push_option,
                     remote,
                     '--set-upstream ' if set_upstream else '',
                     branch
                 ),
                 cwd=local_path
             )
             ))

    prev_results = ()
    for (label, command) in commands:
        (rc, out, err) = command(*prev_results)
        if rc != 0:
            module.fail_json(
                msg="Failed to %s: %s %s" % (label, out, err)
            )
        else:
            prev_results = (rc, out, err)

    module.exit_json(**result)


if __name__ == '__main__':
    main()


