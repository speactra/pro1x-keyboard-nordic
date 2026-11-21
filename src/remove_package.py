"""Explicit, fail-closed package removal for the active local session."""
import os
import pwd
import subprocess
import sys
from service_runtime import active_session, UnsafeState

BASE = '/usr/lib/pro1x-keyboard-nordic/'
ROOT_UNIT = 'pro1x-keyboard-nordic.service'
USER_UNIT = 'pro1x-keyboard-nordic-session.service'


def remove():
    if os.geteuid() != 0:
        raise PermissionError('root is required')
    session = active_session()
    if session is None or session[1] == 0:
        raise UnsafeState('an active non-root local session is required')
    uid = session[1]
    account = pwd.getpwuid(uid)
    user_env = {
        'PATH': '/usr/bin:/bin', 'HOME': account.pw_dir,
        'USER': account.pw_name, 'LOGNAME': account.pw_name,
        'XDG_RUNTIME_DIR': '/run/user/' + str(uid),
        'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/' + str(uid) + '/dbus/user_bus_socket',
        'PYTHONDONTWRITEBYTECODE': '1',
    }

    def drop_privileges():
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(uid)

    def run(args, as_user=False):
        options = {'check': True, 'timeout': 45, 'cwd': '/'}
        if as_user:
            options.update(env=user_env, preexec_fn=drop_privileges)
        subprocess.run(args, **options)

    # Always demand the live authenticated fallback, even if never activated.
    run(['/usr/bin/python3', BASE + 'service_runtime.py', 'prepare-remove'])
    if active_session() != session:
        raise UnsafeState('session changed during removal; restart both services before retry')
    run(['/usr/bin/systemctl', '--user', 'disable', '--now', USER_UNIT], True)
    # Verify/restore the user layout again after the watcher has exited.
    run(['/usr/bin/python3', BASE + 'session_agent.py', 'prepare-remove'], True)
    run(['/usr/bin/systemctl', 'disable', '--now', ROOT_UNIT])
    # Independently verify the original GPIO table after root has stopped.
    run(['/usr/bin/python3', BASE + 'service_runtime.py', 'recover'])


if __name__ == '__main__':
    try:
        remove()
    except Exception as error:
        print('Removal aborted: ' + str(error) +
              '; select a generic layout and restart both services before retry.', file=sys.stderr)
        sys.exit(1)
