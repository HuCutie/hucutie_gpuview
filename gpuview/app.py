#!/usr/bin/env python

"""
Web API of gpuview.

@author Fitsum Gaim
@url https://github.com/fgaim
"""

import os
import json
from datetime import datetime

from bottle import Bottle, TEMPLATE_PATH, template, response

# from . import utils
# from . import core
import utils
import core


app = Bottle()
abs_path = os.path.dirname(os.path.realpath(__file__))
abs_views_path = os.path.join(abs_path, 'views')
TEMPLATE_PATH.insert(0, abs_views_path)


@app.route('/')
def index():
    gpustats = core.all_gpustats()
    now = datetime.now().strftime('Updated at %Y-%m-%d %H-%M-%S')
    return template('index', gpustats=gpustats, update_time=now)


@app.route('/gpustat', methods=['GET'])
def report_gpustat():
    """
    Returns the gpustat of this host.
        See `exclude-self` option of `gpuview run`.
    """

    def _date_handler(obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        else:
            raise TypeError(type(obj))

    response.content_type = 'application/json'
    resp = core.my_gpustat()
    return json.dumps(resp, default=_date_handler)


def main():
    parser = utils.arg_parser()
    args = parser.parse_args()

    if 'run' == args.action:
        app.run(host=args.host, port=args.port, debug=args.debug)
    elif 'service' == args.action:
        core.install_service(host=args.host,
                             port=args.port)
    elif 'add' == args.action:
        core.add_host(args.url, args.name)
    elif 'remove' == args.action:
        core.remove_host(args.url)
    elif 'hosts' == args.action:
        core.print_hosts()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
