import json
import time
import urllib.request
import urllib.error

class TelegramError(Exception):
    def __init__(self,code,description,retry=0):
        self.code,self.description,self.retry=code,description,retry
        super().__init__(f'Telegram {code}: {description}')

class Telegram:
    def __init__(self, token):
        self.base='https://api.telegram.org/bot'+token+'/'
        self.files='https://api.telegram.org/file/bot'+token+'/'

    def call(self,method,**data):
        req=urllib.request.Request(self.base+method,json.dumps(data).encode(),{'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=40) as response:
                result=json.load(response)
        except urllib.error.HTTPError as e:
            result=json.load(e)
        if not result.get('ok'):
            raise TelegramError(result.get('error_code',500),result.get('description','Erreur API'),result.get('parameters',{}).get('retry_after',0))
        return result['result']

    def download(self,file_id,limit=20_000_000):
        f=self.call('getFile',file_id=file_id)
        if f.get('file_size',0)>limit:
            raise ValueError('Média trop volumineux pour analyse complète.')
        with urllib.request.urlopen(self.files+f['file_path'],timeout=30) as response:
            data=response.read(limit+1)
        if len(data)>limit:
            raise ValueError('Média trop volumineux pour analyse complète.')
        return data
