import kagglehub
import pandas as pd
import os
import time
from geopy.geocoders import Nominatim
import openmeteo_requests
import requests_cache
from retry_requests import retry

dataset_dir = kagglehub.dataset_download("pythonafroz/mega-watt-hour-net-energy-for-load-data")
df_list = []
for file_name in os.listdir(dataset_dir):
    if file_name.endswith('.csv'):
        file_path = os.path.join(dataset_dir, file_name)
        
        # Read each file and append to our list
        temp_df = pd.read_csv(file_path, encoding="latin1")
        df_list.append(temp_df)
df = pd.concat(df_list, ignore_index=True)
df['datetime_beginning_utc'] = pd.to_datetime(df['datetime_beginning_utc'])
df['eastern_time_zone'] = df['datetime_beginning_utc'].dt.tz_localize('UTC').dt.tz_convert('US/Eastern')

pjm_office_map = {
    'AECO': 'Atlantic City, NJ',
    'AEPAPT': 'Roanoke, VA',
    'AEPIMP': 'Fort Wayne, IN',
    'AEPKPT': 'Ashland, KY',
    'AEPOPT': 'Columbus, OH',
    'AP': 'Greensburg, PA',
    'BC': 'Baltimore, MD',
    'CE': 'Chicago, IL',
    'DAY': 'Dayton, OH',
    'DEOK': 'Cincinnati, OH',
    'DOM': 'Richmond, VA',
    'DPLCO': 'Wilmington, DE',
    'DUQ': 'Pittsburgh, PA',
    'EASTON': 'Easton, MD',
    'EKPC': 'Winchester, KY',
    'JC': 'Morristown, NJ',
    'ME': 'Reading, PA',
    'OE': 'Akron, OH',
    'OVEC': 'Piketon, OH',
    'PAPWR': 'Park Ridge, NJ',
    'PE': 'Philadelphia, PA',
    'PEPCO': 'Washington, DC',
    'PLCO': 'Allentown, PA',
    'PN': 'Erie, PA',
    'PS': 'Newark, NJ',
    'RECO': 'Mahwah, NJ',
    'SMECO': 'Hughesville, MD',
    'UGI': 'Denver, PA',
    'VMEU': 'Vineland, NJ',
    'RTO': 'Valley Forge, PA'  # This represents the entire PJM footprint combined
}
df['PJM_Office'] = df['load_area'].map(pjm_office_map)

cache_session = requests_cache.CachedSession('.cache', expire_after=-1)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)
geolocator = Nominatim(user_agent="pjm_weather_extractor")
unique_cities = list(set(pjm_office_map.values()))
city_coords = {}
for city in unique_cities:
    try:
        geo_res = geolocator.geocode(city)
        if geo_res:
            city_coords[city] = (geo_res.latitude, geo_res.longitude)
        time.sleep(1)  # Polite rate limit for Nominatim
    except Exception as e:
        print(f"Skipping {city} due to error: {e}")
weather_records = []
url = "https://archive-api.open-meteo.com/v1/archive"

for city, coords in city_coords.items():
    print(f" -> Fetching data for {city}...")
    
    params = {
        "latitude": coords[0],
        "longitude": coords[1],
        "start_date": "2018-01-01",
        "end_date": "2024-12-31",
        "daily": ["temperature_2m_max", "temperature_2m_min", "relative_humidity_2m_max", "relative_humidity_2m_min"],
        "temperature_unit": "fahrenheit", # Matches standard US utility metrics
        "timezone": "America/New_York"
    }
    
    try:
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        daily = response.Daily()
        
        # Build date range array based on response metadata
        date_range = pd.date_range(
            start=pd.to_datetime(daily.Time(), unit="s", utc=True),
            end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=daily.Interval()),
            inclusive="left"
        ).tz_convert(params["timezone"]).tz_localize(None)
        
        # Structure into a temporary dataframe block
        city_df = pd.DataFrame({
            'date': date_range.date, # Keep as standard Date format for clean merging
            'PJM_Office': city,
            'temp_max': daily.Variables(0).ValuesAsNumpy(),
            'temp_min': daily.Variables(1).ValuesAsNumpy(),
            'humidity_max': daily.Variables(2).ValuesAsNumpy(),
            'humidity_min': daily.Variables(3).ValuesAsNumpy()
        })
        
        weather_records.append(city_df)
        
    except Exception as e:
        print(f"Failed to fetch data for {city}: {e}")
master_weather_df = pd.concat(weather_records, ignore_index=True)

df['date'] = df['eastern_time_zone'].dt.date
final_df = pd.merge(df, master_weather_df, on=['date', 'PJM_Office'], how='left')
final_df.to_csv("pjm_load_weather_merged.csv", index=False)