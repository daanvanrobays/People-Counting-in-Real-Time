import json
import requests

# initiate features config.
with open("utils/config.json", "r") as file:
    config = json.load(file)


class Api:
    """ Class to initiate the api function. """

    def __init__(self):
        self.url = config["Api_Url"]

    def post(self, total: int, total_down: int, total_up: int, delta: int):
        print(f"API - total: {total}, total_down: {total_down}, total_up: {total_up}, delta: {delta} ")
        post_body = {'apparaat': config["Device"], 'binnen': total_down, 'buiten': total_up, 'delta': delta,
                     'totaal': total}
        resp = requests.post(self.url, json=post_body)
        print(resp.text)
