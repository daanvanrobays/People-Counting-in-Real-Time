import json
import requests

# initiate features config.
with open("utils/config.json", "r") as file:
    config = json.load(file)


class Api:
    """ Class to initiate the api function. """

    def __init__(self):
        self.url = config["Api_Url"]

    def post(self, total, move_in, in_time, move_out, out_time):
        post_body = {'total': total, 'move_in': move_in, 'in_time': in_time, 'move_out': move_out, 'out_time': out_time}
        requests.post(self.url, json=post_body)
