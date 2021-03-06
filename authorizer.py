import argparse
import configparser
import errno
import json
import logging
import re
import subprocess
import traceback

import scitokens
from flask import Flask, request, Response

logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)

g_authorized_issuers = {}
g_global_audience = ""


@app.route('/auth')
def flask_listener():
    # Convert the operation to something that the token will know,
    # like read or write
    orig_op = request.headers.get('X-Original-Method')
    op = ""
    if orig_op == "GET":
        op = 'read'
    elif orig_op in ["PUT", "POST", "DELETE", "MKCOL", "COPY", "MOVE"]:
        op = 'write'

    # Look at the path as well
    orig_path = request.headers.get('X-Original-URI')
    
    # Convert the token to a SciToken (also check for errors with the token)
    if 'Authorization' not in request.headers:
        resp = Response("No Authorization header")
        resp.headers['WWW-Authenticate'] = 'Bearer realm="scitokens"'
        logging.error("No Authorization header presented")
        return resp, 401
    raw_token = request.headers['Authorization'].split(" ", 1)[1]
    
    # Convert the token
    # Send a 401 error code if there is any problem
    try:
        token = scitokens.SciToken.deserialize(raw_token, audience=g_global_audience)
    except Exception as e:
        resp = Response("No Authorization header")
        resp.headers['WWW-Authenticate'] = \
            'Bearer realm="scitokens",error="invalid_token",error_description="{0}"'.format(str(e))
        logging.exception("Failed to deserialize SciToken")
        traceback.print_exc()
        return resp, 401
    
    (successful, message) = test_operation_path(op, orig_path, token)
    if successful:
        if 'jti' in token._claims:
            logging.info("Allowed token with Token ID: {0}".format(str(token['jti'])))
        return message, 200
    else:
        if 'jti' in token._claims:
            logging.error("Failed to authenticate SciToken ID {0} because {1}".format(token['jti'],
                                                                                      message))
        else:
            logging.error("Failed to authenticate SciToken because {0}".format(message))
        return message, 403
    

def test_operation_path(op, path, token):
    """
    Test whether an operation and path is allowed by this scitoken.
    
    :returns: (successful, message) true if the scitoken allows for this path & op, else false
    """
    # Setup a SciToken Enforcer
    if token['iss'] not in g_authorized_issuers:
        return False, "Issuer not in configuration"
    issuer_url = token['iss']
    issuer = g_authorized_issuers[issuer_url]
    base_path = issuer['base_path']
    
    # The path above should consist of"
    # $base_path + / + $auth_path + / + $request_path = path
    if not path.startswith(base_path):
        logging.error("Requested path does not start with base_path")
        return False, "The requested path does not start with the base path"
    
    # Now remove the base path so we just get the auth_path + request_path
    filepath_on_disk = path.replace(base_path, "", 1)

    if issuer['use_impersonation']:
        if impersonation_test(token, op, filepath_on_disk):
            return True, ""
        # Note: Fall through to next authorizer

    enforcer = scitokens.scitokens.Enforcer(token['iss'], audience=g_global_audience)
    try:
        if enforcer.test(token, op, filepath_on_disk):
            return True, ""
        else:
            return False, "Path not allowed"
    except scitokens.scitokens.EnforcementError as e:
        print(e)
        return False, str(e)


# From xrootd-scitokens, we want the same configuration
def config(fname):
    print("Trying to load configuration from %s" % fname)
    cp = configparser.ConfigParser()
    try:
        with open(fname, "r") as fp:
            cp.read_file(fp)
    except IOError as ie:
        if ie.errno == errno.ENOENT:
            return
        raise
    for section in cp.sections():
        if not section.lower().startswith("issuer "):
            continue
        if 'issuer' not in cp.options(section):
            print("Ignoring section %s as it has no `issuer` option set." % section)
            continue
        if 'base_path' not in cp.options(section):
            print("Ignoring section %s as it has no `base_path` option set." % section)
            continue
        issuer = cp.get(section, 'issuer')
        base_path = cp.get(section, 'base_path')
        base_path = scitokens.urltools.normalize_path(base_path)
        issuer_info = g_authorized_issuers.setdefault(issuer, {})
        issuer_info['base_path'] = base_path
        issuer_info['use_impersonation'] = cp.getboolean(section, 'impersonation', fallback=False)
        if 'map_subject' in cp.options(section):
            issuer_info['map_subject'] = cp.getboolean(section, 'map_subject')
        print("Configured token access for %s (issuer %s): %s" %
              (section, issuer, str(issuer_info)))
    
    global g_global_audience
    if 'audience_json' in cp.options("Global"):
        # Read in the audience as json.  Hopefully it's in list format or a string
        g_global_audience = json.loads(cp.get("Global", "audience_json"))
    elif 'audience' in cp.options("Global"):
        g_global_audience = cp.get("Global", "audience")
        if ',' in g_global_audience:
            # Split the audience list
            g_global_audience = re.split("\s*,\s*", g_global_audience)
    

def impersonation_test(token, op, filepath_on_disk):
    test_option = "-w" if op == "write" else "-r"
    return_code = subprocess.call(["sudo", "-u", token["sub"],
                                   "test", test_option, filepath_on_disk])
    return return_code == 0


def main():
    
    parser = argparse.ArgumentParser(description='Authenticate HTTP Requests')
    parser.add_argument('-c', '--config', dest='config', type=str, 
                        default="/etc/scitokens/authorizer.cfg",
                        help="Location of the configuration file")

    args = parser.parse_args()
    
    # Read in configuration
    config(args.config)
    
    # Set up listener for events
    app.run(host='localhost', port=1234)


if __name__ == "__main__":
    main()
