# -*- coding: utf-8 -*-
"""
    nereid_passbook.py

    :copyright: (c) 2015 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
import dateutil.parser
from uuid import uuid4
from collections import defaultdict

from trytond.model import ModelView, ModelSQL, fields
from trytond.pool import PoolMeta, Pool
from trytond.config import config
from trytond.tools import file_open
from nereid import request, route, abort, jsonify, url_for, redirect
from flask import send_file


__metaclass__ = PoolMeta
__all__ = ['Pass', 'Registration']


class Pass(ModelSQL, ModelView):
    "Passes"
    __name__ = 'nereid.passbook.pass'

    # The record for which this pass stands out for
    origin = fields.Reference(
        'Origin', selection='_get_origin', required=True
    )

    # This token is sent back from the devices to verify the authenticity of
    # update and register calls. The serial number is the ID of the pass itself
    authentication_token = fields.Char(
        'Authentication Token', required=True, readonly=True
    )

    registrations = fields.One2Many(
        'nereid.passbook.registration', 'pass_', 'Registrations'
    )

    last_update = fields.Function(
        fields.DateTime('Last Updated Date'), 'get_last_update'
    )

    active = fields.Boolean('Active', select=True)

    @staticmethod
    def default_active():
        return True

    @classmethod
    def get_origin(cls):
        '''
        Return list of Model names for origin Reference.

        Downstream modules should extend this and inject the models it wants
        to add to the possible origins.
        '''
        return []

    @classmethod
    def _get_origin(cls):
        """
        Adds models to the origin field.

        .. tip::

            Do not override this. Adding models to this is better done by
            adding the model name to ``get_origin`` where only the name of the
            model is required.
        """
        IrModel = Pool().get('ir.model')
        models = cls.get_origin()
        models = IrModel.search([('model', 'in', models)])
        return [(None, '')] + [(m.model, m.name) for m in models]

    @staticmethod
    def default_authentication_token():
        return str(uuid4())

    def get_last_update(self, name=None):
        """
        Return the last updated date of the record
        """
        return self.origin.write_date or self.origin.create_date

    def check_authorization(self, authorization=None):
        """
        Ensures that the authorization in the current request is valid.
        Aborts if its invalid.

        if authorization is None, check for the authorization header sent
        by apple passbook.
        """
        if authorization is None:
            # validate the authentication token
            # The Authorization header is supplied; its value is the word
            # "ApplePass", followed by a space, followed by the
            # authorization token as specified in the pass.
            _, authorization = request.headers['Authorization'].split(' ')

        if authorization != self.authentication_token:
            abort(401)

    @route('/passbook')
    def webservice_url(cls):
        """
        A dummy endpoint just for the sake of generating the webServiceURL
        """
        return redirect(url_for('nereid.website.home'))

    def make_pkpass(self):
        """
        Builds a pass by calling the origin model for it and then adds a serial
        number and signs it and builds the pkpass file.
        """
        passfile = self.origin.get_passfile()

        # Add information to be provided by this API
        passfile.serialNumber = str(self.id)
        passfile.webServiceURL = url_for(
            'nereid.passbook.pass.webservice_url', _external=True, _secure=True
        )
        passfile.authenticationToken = self.authentication_token

        # TODO: What to do if barcode is not there ?

        return passfile.create(
            config.get('nereid_passbook', 'certificate'),
            config.get('nereid_passbook', 'key'),
            file_open('nereid_passbook/wwdr.pem').name,
            '',     # Password for pem file ?
        )

    @route('/passbook/<int:active_id>', methods=['POST', 'GET'])
    def download(self):
        """
        Download pk_pass file for the pass.
        """
        self.check_authorization(request.values['authentication_token'])
        zipfile = self.make_pkpass()
        zipfile.seek(0)
        return send_file(
            zipfile,
            attachment_filename='pass.pkpass',
            as_attachment=True,
            mimetype='application/vnd.apple.pkpass'
        )

    @route('/passbook/<version>/passes/<pass_type>/<int:active_id>')
    def get_latest_version(self, pass_type=None, version=None):
        """
        Return the latest version of the pass.

        This method is for use from passbook app and not from a web app. For
        downloading from the webapp use the `download` handler as it does not
        require the authorization on the header, but as a url argument.
        """
        self.check_authorization()
        zipfile = self.make_pkpass()
        zipfile.seek(0)
        return send_file(
            zipfile,
            attachment_filename='pass.pkpass',
            as_attachment=True,
            mimetype='application/vnd.apple.pkpass'
        )

    @route(
        '/passbook/<version>/devices/<device>/registrations/<pass_type>' +
        '/<int:active_id>',
        methods=['POST', 'DELETE']
    )
    def register_device(self, device, pass_type=None, version=None):
        """
        Register the device against this pass

        :pass_type: The pass_type is ignored because serial number sent as
                    active_id is unique enough to identify the pass
        """
        Registration = Pool().get('nereid.passbook.registration')

        self.check_authorization()
        push_token = request.json['pushToken']

        # Check if a registration already exists
        regns = Registration.search([
            ('pass_', '=', self.id),
            ('device_library_identifier', '=', device)
        ])
        if regns:
            if request.method == 'DELETE':
                Registration.delete(regns)
            return '', 200

        if request.method == 'DELETE':
            # Requested deletion of a registration that does not exist
            abort(404)

        # No regn. Create a new registration
        regn = Registration(
            pass_=self,
            device_library_identifier=device,
            push_token=push_token,
        )
        regn.save()
        return '', 201

    @route('/passbook/<version>/log', methods=['POST'])
    def log(cls, version=None):
        """
        Capture and then spit the logs to STDERR
        """
        # TODO: Implement a log model where all logs sent from the passbook
        # would be stored
        print request.json['logs']


class Registration(ModelSQL, ModelView):
    "Pass Registrations"
    __name__ = 'nereid.passbook.registration'

    pass_ = fields.Many2One(
        'nereid.passbook.pass', 'Pass', select=True, required=True
    )

    # The unique identifier of the device where the passbook is registered
    device_library_identifier = fields.Char(
        'Device Library Ident.', required=True, select=True
    )

    # The token needed if we were to send a push notification for the
    # update of the pass
    push_token = fields.Char('Push Token')

    @classmethod
    @route('/passbook/<version>/devices/<device>/registrations/<pass_type>')
    def get_passes(cls, device, pass_type=None, version=None):
        """
        Getting the Serial Numbers for Passes Associated with the Device
        """
        domain = [
            ('device_library_identifier', '=', device),
            ('pass_.active', '=', True),
        ]
        updated_since = request.args.get(
            'passesUpdatedSince', type=dateutil.parser.parse
        )

        passes = set()
        for registration in cls.search(domain):
            if updated_since is not None and \
                    registration.pass_.last_update < updated_since:
                # If updated_since is specified and the last_update of the
                # pass is before it, there is nothing more to send
                continue
            passes.add(registration.pass_)

        if not passes:
            return '', 204

        rv = defaultdict(list)
        for pass_ in passes:
            rv[pass_.last_update.isoformat(' ')].append(str(pass_.id))
        return jsonify(rv), 200
