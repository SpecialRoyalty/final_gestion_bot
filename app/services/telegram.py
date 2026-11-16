import json
import time
import urllib.request
import urllib.error

class APIError(Exception):
    def __init__(self, data):
        self.code = data.get('error_code', 0)
        self.retry = data.get('parameters', {}).get('retry_after', 0)
        super().__init__(data.get('description', 'Telegram unavailable'))

class NetworkError(APIError):
    """Delivery may be ambiguous; never blindly repeat a non-idempotent send."""
    def __init__(self,method):
        self.method=method
        super().__init__({'error_code':0,'description':'Connexion Telegram interrompue ('+method+'). Résultat non confirmé ; aucun groupe déclaré supprimé.'})

class Telegram:
    SAFE_PREFIXES=('get','edit','set','delete','revoke','unban','ban','restrict','approve','decline')
    def __init__(self, token):
        self.base = 'https://api.telegram.org/bot' + token + '/'

    def call(self, method, **data):
        request = urllib.request.Request(self.base + method,
            data=json.dumps(data).encode(), headers={'Content-Type':'application/json'})
        attempts=3 if method.startswith(self.SAFE_PREFIXES) else 1
        for attempt in range(attempts):
            try:
                try:
                    with urllib.request.urlopen(request, timeout=45) as response:
                        result = json.load(response)
                except urllib.error.HTTPError as error:
                    try: result = json.load(error)
                    except (ValueError,OSError): result={'ok':False,'error_code':error.code,'description':'Telegram HTTP '+str(error.code)}
                if not result.get('ok'): raise APIError(result)
                return result['result']
            except (urllib.error.URLError,ConnectionError,TimeoutError) as error:
                if attempt+1>=attempts: raise NetworkError(method) from None
                time.sleep(0.5*(2**attempt))
