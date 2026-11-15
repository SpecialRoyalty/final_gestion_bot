import json
import urllib.request
import urllib.error

class APIError(Exception):
    def __init__(self, data):
        self.code = data.get('error_code', 0)
        self.retry = data.get('parameters', {}).get('retry_after', 0)
        super().__init__(data.get('description', 'Telegram unavailable'))

class Telegram:
    def __init__(self, token):
        self.base = 'https://api.telegram.org/bot' + token + '/'

    def call(self, method, **data):
        request = urllib.request.Request(self.base + method,
            data=json.dumps(data).encode(), headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            result = json.load(error)
        if not result.get('ok'): raise APIError(result)
        return result['result']
