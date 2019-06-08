from base64 import b64encode
from os import path, environ
from shutil import copyfile
from urllib.parse import urlparse

import boto3
import requests


class StoreManager:
    def __init__(self, secrets=None, stores={}):
        self._stores = {}
        self._secrets = secrets
        self._from_dict(stores)

    # todo: read stores from input spec
    def _from_dict(self, stores={}):
        pass

    def secret(self, key):
        self._secrets.get(key)

    def add_store(self, store):
        self._stores[store.name] = store

    def get_or_create_store(self, url):
        store = None
        p = urlparse(url)
        schema = p.scheme.lower()
        endpoint = p.hostname
        if p.port:
            endpoint += ':{}'.format(p.port)

        if not schema and endpoint:
            if endpoint in self._stores.keys():
                return self._stores[endpoint], p.path
            else:
                raise ValueError('no such store ({})'.format(endpoint))

        storekey = '{}://{}'.format(schema, endpoint)
        if storekey in self._stores.keys():
            return self._stores[storekey], p.path

        store = self._schema_to_store(schema)
        new_store = store(self, storekey, endpoint)
        self._stores[storekey] = new_store
        return new_store, p.path

    def _schema_to_store(self, schema):
        if not schema or schema == 'file':
            return FileStore
        elif schema == 's3':
            return S3Store
        else:
            raise ValueError('unsupported store scheme ({})'.format(schema))


class DataStore:
    def __init__(self, parent: StoreManager, name, endpoint=''):
        self._parent = parent
        self.kind = ''
        self.name = name
        self.endpoint = endpoint
        self.subpath = ''
        self.secret_pfx = ''
        self.options = {}

    def _join(self, key):
        return path.join(self.subpath, key)

    def _secret(self, key):
        return self._parent.secret(self.secret_pfx + key)

    @property
    def url(self):
        return '{}://{}/'.format(self.kind, self.endpoint)

    def get(self, key):
        pass

    def put(self, key, data):
        pass

    def download(self, key, target_path):
        text = self.get(key)
        with open(target_path, 'w') as fp:
            fp.write(text)
            fp.close()

    def upload(self, key, src_path):
        pass


class FileStore(DataStore):
    def __init__(self, parent: StoreManager, name, endpoint=''):
        super().__init__(parent, name, endpoint)
        self.kind = 'file'

    @property
    def url(self):
        return self.subpath

    def get(self, key):
        with open(self._join(key), 'r') as fp:
            return fp.read()

    def put(self, key, data):
        with open(self._join(key), 'w') as fp:
            fp.write(data)
            fp.close()

    def download(self, key, target_path):
        fullpath = self._join(key)
        if fullpath == target_path:
            return
        copyfile(fullpath, target_path)

    def upload(self, key, src_path):
        copyfile(src_path, self._join(key))


class S3Store(DataStore):
    def __init__(self, parent: StoreManager, name, endpoint=''):
        super().__init__(parent, name, endpoint)
        self.kind = 's3'
        region = None

        access_key = self._secret('AWS_ACCESS_KEY_ID')
        secret_key = self._secret('AWS_SECRET_ACCESS_KEY')

        if access_key or secret_key:
            self.s3 = boto3.resource('s3', region_name=region,
                                     aws_access_key_id=access_key,
                                     aws_secret_access_key=secret_key)
        else:
            # from env variables
            self.s3 = boto3.resource('s3', region_name=region)

    def upload(self, key, src_path):
        self.s3.Object(self.endpoint, self._join(key)).put(Body=open(src_path, 'rb'))

    def get(self, key):
        obj = self.s3.Object(self.endpoint, self._join(key))
        return obj.get()['Body'].read()

    def put(self, key, data):
        self.s3.Object(self.endpoint, self._join(key)).put(Body=data)


def basic_auth_header(user, password):
    username = user.encode('latin1')
    password = password.encode('latin1')
    base = b64encode(b':'.join((username, password))).strip()
    authstr = 'Basic ' + base.decode('ascii')
    return {'Authorization': authstr}


def http_get(url, headers=None, auth=None):
    try:
        resp = requests.get(url, headers=headers, auth=auth)
    except OSError:
        raise OSError('error: cannot connect to {}'.format(url))

    if not resp.ok:
        raise OSError('failed to read file in {}'.format(url))
    return resp.text


def http_put(url, data, headers=None, auth=None):
    try:
        resp = requests.put(url, data=data, headers=headers, auth=auth)
    except OSError:
        raise OSError('error: cannot connect to {}'.format(url))
    if not resp.ok:
        raise OSError(
            'failed to upload to {} {}'.format(url, resp.status_code))


def http_upload(url, file_path, headers=None, auth=None):
    with open(file_path, 'rb') as data:
        http_put(url, data, headers, auth)


class HttpStore(DataStore):
    def __init__(self, parent: StoreManager, name, endpoint=''):
        super().__init__(parent, name, endpoint)
        self.kind = 'http'
        self.auth = None

    def upload(self, key, src_path):
        raise ValueError('unimplemented')

    def put(self, key, data):
        raise ValueError('unimplemented')

    def get(self, key):
        return http_get(self.url + self._join(key), None, self.auth)


class V3ioStore(DataStore):
    def __init__(self, parent: StoreManager, name, endpoint=''):
        super().__init__(parent, name, endpoint)
        self.kind = 'v3io'
        self.endpoint = self.endpoint or environ.get('V3IO_API')

        token = self._secret('V3IO_ACCESS_KEY') or environ.get('V3IO_ACCESS_KEY')
        username = self._secret('V3IO_USERNAME') or environ.get('V3IO_USERNAME')
        password = self._secret('V3IO_PASSWORD') or environ.get('V3IO_PASSWORD')

        self.headers = None
        self.auth = None
        if token:
            self.headers = {'X-v3io-session-key': token}
        elif username and password:
            self.headers = basic_auth_header(username, password)

    def upload(self, key, src_path):
        http_upload(self.url + self._join(key), src_path, self.headers, None)

    def get(self, key):
        return http_get(self.url + self._join(key), self.headers, None)

    def put(self, key, data):
        http_put(self.url + self._join(key), data, self.headers, None)
