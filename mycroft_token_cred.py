"""
oauth2client token credentials class for updating token through the mycroft
backend as needed.
"""

from requests import HTTPError

from mycroft.api import DeviceApi
from oauth2client import client
from .local_save import LocalSave
from mycroft.util.log import LOG



class MycroftTokenCredentials(client.AccessTokenCredentials):
    def __init__(self, cred_id):
        self.cred_id = cred_id
        self.local_save = LocalSave()
        d = self.get_credentials()
        super().__init__(d['access_token'], d['user_agent'])
        

    def get_credentials(self):
        """Get credentials through backend.

        Will do a single retry for if an HTTPError occurs.

        Returns:
            dict with data received from backend
        """
        retry = False
        try:
            d = DeviceApi().get_oauth_token(self.cred_id)
            self.save_local(d)  
        except HTTPError:
            retry = True
        if retry:
            d = DeviceApi().get_oauth_token(self.cred_id)
            self.save_local(d)
        return d

    def _refresh(self, http):
        """Override to handle refresh through mycroft backend."""
        d = self.get_credentials()
        self.access_token = d['access_token']

    
    def save_local(self, data):
        data = {
            "access_token":data['access_token'],
            "user_agent":data['user_agent']
            }
        self.local_save.update_file(data)
        d = self.local_save.get_contents()
        LOG.info(d)


