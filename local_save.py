import os
import json as js
import datetime as dt

class LocalSave:
    def __init__(self, name = "calendar_credentials"):
        self.file_name = (name+".txt")
        self.file = open(self.file_name,"a")
        self.content = {}
        self.entry_name = "Entries"
        self.content[self.entry_name] = []
        self.amount_of_entries = 0

        if os.path.isfile(self.file_name): # Check if sms log is already created
            self.check_entries()
            self.set_content()

    def check_entries(self): # To check the amount of entries in the sms log
        try:
            with open(self.file_name) as json_file:
                data = js.load(json_file)
                try: 
                    self.amount_of_entries = len(data[self.entry_name])
                except:
                    print("[Info] Currently no entries")
        except:
            print("[Warning] File doesn't exist yet")

    def set_content(self):  # Add content from the local file to local variable
        try:
            with open(self.file_name) as json_file:
                data = js.load(json_file)
                for i in range(self.amount_of_entries):
                    self.content[self.entry_name].append(data[self.entry_name][i])
        except:
            print("[Warning] contents not found")
                
    def update_file(self, content): # Updates the localfile by overwritting the current file content , add dictonary to the param to store on a local file
        
        if content == {}:
            return 0

        temp_content = content

        temp_dict = {         
            
            'access_token': temp_content['access_token'],
            'user_agent': temp_content['user_agent']
            }

        self.content[self.entry_name].append(temp_dict)

        with open(self.file_name,'w+') as outfile: # Overwrite content from the sms log
            js.dump(self.content,outfile, indent= 2) 

    def get_contents(self): # get content from local file and returns it in a list.
        temp_list = []
        try:
            with open(self.file_name) as json_file:
                data = js.load(json_file)
                for i in range(self.amount_of_entries):
                    temp_list.append(data[self.entry_name][i])
        except:
            print("[Warning] Empty")
        return temp_list # Returns a list of logs