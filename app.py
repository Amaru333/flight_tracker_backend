from flask import Flask, Response, request
from pymongo import MongoClient
from bson import json_util
import json
import requests
from flask_apscheduler import APScheduler
from bson import ObjectId
from bson.timestamp import Timestamp
from flask_cors import CORS
import os

from datetime import datetime

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": ["http://localhost:3000", "https://flight-tracker-frontend.vercel.app"]}})

sched = APScheduler()

client = MongoClient(os.getenv('MONGODB'))
db = client['flights']
collection = db['itinerary']
current_date = datetime.now()

def get_cheapest_price(origin, destination, date):
    url = "https://skyscanner-api.p.rapidapi.com/v3e/flights/live/search/synced"
    payload = {
        "query": {
            "market": "US",
            "locale": "en-GB",
            "currency": "USD",
            "queryLegs": [
                {
                    "originPlaceId": {
                        "iata": origin
                    },
                    "destinationPlaceId": {
                        "iata": destination
                    },
                    "date": {
                        "year": int(date[0:4]),
                        "month": int(date[4:6]),
                        "day": int(date[6:8])
                    }
                }
            ],
            "cabinClass": "CABIN_CLASS_ECONOMY",
            "adults": 1
        }
    }
    headers = {
        'X-RapidAPI-Key': os.getenv('RAPID_API'),
        'X-RapidAPI-Host': 'skyscanner-api.p.rapidapi.com'
    }
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        results = response.json()
        cheapest_itinerary = results['content']['sortingOptions']['cheapest'][0]['itineraryId']
        cheapest_itinerary_array = results['content']['results']['itineraries'][cheapest_itinerary]
        cheapest_itinerary_data = cheapest_itinerary_array['pricingOptions'][0]['items'][0]
        cheapest_itinerary_leg = cheapest_itinerary_array['legIds'][0]
        cheapest_itinerary_leg_array = results['content']['results']['legs'][cheapest_itinerary_leg]
        cheapest_itinerary_carrier_id = cheapest_itinerary_leg_array['marketingCarrierIds'][0]

        result_dict = {
            'current_cheapest_price': float(cheapest_itinerary_data['price']['amount']),
            'link': cheapest_itinerary_data['deepLink'],
            'dept_time': cheapest_itinerary_leg_array['departureDateTime'],
            'arr_time': cheapest_itinerary_leg_array['arrivalDateTime'],
            'stops': cheapest_itinerary_leg_array['stopCount'],
            'duration': cheapest_itinerary_leg_array['durationInMinutes'],
            'carrier': results['content']['results']['carriers'][cheapest_itinerary_carrier_id]
        }
        return result_dict
    else:
        print(f'Request failed with status code {response.status_code}: {response.text}')

@app.route('/flight-price')
def flight_price():
    print(current_date)
    origin = request.args.get('origin')
    destination = request.args.get('destination')
    date = request.args.get('date')
    # email = request.args.get('email')
    # phone_number = request.args.get('phone_number')
    price_data = collection.find_one({'origin': origin, 'destination': destination, 'date': date})

    if price_data:
        # if not any(d['phone_number'] == phone_number for d in price_data['subscribed_users']):
        #     print(price_data["_id"])
        #     collection.update_one({ '_id': ObjectId(price_data["_id"])}, {'$push' : {'subscribed_users': {
        #         'phone_number': phone_number,
        #         'email': email
        #     }}})
        # else:
        #     pass
        price_data_without_users = price_data
        price_data_without_users.pop("subscribed_users")
        price_json = json.loads(json_util.dumps(price_data_without_users))
        response_json = json.dumps(price_json)
        return Response(response_json, content_type='application/json')
    else:
        price_data = get_cheapest_price(origin, destination, date)
        data = {
            'origin': origin,
            'destination': destination,
            'date': date,
            'currency': 'USD',
            'tracked_min_price': price_data['current_cheapest_price'],
            'link': price_data['link'],
            'dept_time': price_data['dept_time'],
            'arr_time': price_data['arr_time'],
            'stops': price_data['stops'],
            'duration': price_data['duration'],
            'carrier': price_data['carrier'],
            'price_update_time': Timestamp(int(datetime.today().timestamp()), 1),
            'subscribed_users': [
                # {
                #     'phone_number': phone_number,
                #     'email': email
                # }
            ]
        }
        result = collection.insert_one(data)
        inserted_id = result.inserted_id
        inserted_data = collection.find_one({'_id': inserted_id}, {'subscribed_users': 0})
        price_json = json.loads(json_util.dumps(inserted_data))
        response_json = json.dumps(price_json)
        return Response(response_json, content_type='application/json')
    
@app.route('/flight-price-subscribe')
def flight_price_subscribe():
    email = request.args.get('email')
    phone_number = request.args.get('phone_number')
    origin = request.args.get('origin')
    destination = request.args.get('destination')
    date = request.args.get('date')
    price_data = collection.find_one({'origin': origin, 'destination': destination, 'date': date})
    if not price_data['subscribed_users'] or not any(d['phone_number'] == phone_number or d['email'] == email for d in price_data['subscribed_users']):
        print(price_data["_id"])
        collection.update_one({ '_id': ObjectId(price_data["_id"])}, {'$push' : {'subscribed_users': {
            'phone_number': phone_number,
            'email': email
        }}})
        return "Subscribed Successfully"
    else:
        return "Already Subscribed"

def notify_users(users, currency, old_price, new_price, new_link, origin, destination, date):
    url = "https://api.courier.com/send"
    payload = {
    "message": {
        "content": {
        "title": "Price drop on flight " + origin + "-" + destination,
        "body": "The flight from " + origin + " to " + destination + " on " + str(date[0:4]) + "/" + str(date[4:6]) + "/" + str(date[6:8]) + " has dropped from " + currency + " " + str(old_price) + " to " + currency + " " + str(new_price) + ". Click on the following link to book the flight: " + new_link
        },
        "routing": {
        "method": "all",
        "channels": [
            "email",
            "sms"
        ]
        },
        "to": users
    }
    }
    headers = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": "Bearer pk_prod_W20VYRQY3943K5H2RNKN9ZBGB24Q"
    }
    response = requests.request("POST", url, json=payload, headers=headers)
    print(response.text)

def check_flight_price():
    for document in collection.find():
        if not document['subscribed_users']:
            result = collection.find_one_and_delete({"_id": document["_id"]})
            print(result)
        else:
            mentioned_date = document['date']
            mentioned_date_timestamp = datetime(int(mentioned_date[0:4]), int(mentioned_date[4:6]), int(mentioned_date[6:8]), 23, 59, 59)
            if current_date > mentioned_date_timestamp:
                result = collection.find_one_and_delete({"_id": document["_id"]})
                print(result)
            else:
                new_data = get_cheapest_price(document['origin'], document['destination'], document['date'])
                if new_data['current_cheapest_price'] < document['tracked_min_price']:
                    collection.update_one({'_id': document["_id"]}, {'$set': {'tracked_min_price': new_data['current_cheapest_price'], 'link': new_data['link'], 'price_update_time': Timestamp(int(datetime.today().timestamp()), 1), 'dept_time': new_data['dept_time'], 'arr_time': new_data['arr_time'], 'stops': new_data['stops'], 'duration': new_data['duration'], 'carrier': new_data['carrier']}})
                    notify_users(document['subscribed_users'], document['currency'], document['tracked_min_price'], new_data['current_cheapest_price'], new_data['link'], document['origin'], document['destination'], document['date'])

if __name__ == '__main__':
    sched.add_job(id="GetPrices", func=check_flight_price, trigger="cron", day_of_week="mon-sun", hour=14, minute=50)
    sched.start()
    app.run(debug=True, use_reloader = False)