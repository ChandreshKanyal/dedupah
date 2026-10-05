"""Indian geography reference: city names/renames -> canonical city, city -> state. Real-world knowledge only."""

CITY_STATE = {
    "mumbai": "maharashtra", "pune": "maharashtra", "nagpur": "maharashtra", "thane": "maharashtra",
    "navi mumbai": "maharashtra", "nashik": "maharashtra", "aurangabad": "maharashtra",
    "delhi": "delhi", "gurugram": "haryana", "faridabad": "haryana", "noida": "uttar pradesh",
    "ghaziabad": "uttar pradesh", "lucknow": "uttar pradesh", "kanpur": "uttar pradesh", "agra": "uttar pradesh",
    "varanasi": "uttar pradesh", "meerut": "uttar pradesh", "prayagraj": "uttar pradesh",
    "bengaluru": "karnataka", "mysuru": "karnataka", "mangaluru": "karnataka", "hubballi": "karnataka",
    "chennai": "tamil nadu", "coimbatore": "tamil nadu", "madurai": "tamil nadu", "tiruchirappalli": "tamil nadu",
    "hyderabad": "telangana", "warangal": "telangana", "visakhapatnam": "andhra pradesh", "vijayawada": "andhra pradesh",
    "kolkata": "west bengal", "howrah": "west bengal", "ahmedabad": "gujarat", "surat": "gujarat",
    "vadodara": "gujarat", "rajkot": "gujarat", "jaipur": "rajasthan", "jodhpur": "rajasthan", "udaipur": "rajasthan",
    "chandigarh": "chandigarh", "mohali": "punjab", "ludhiana": "punjab", "amritsar": "punjab",
    "kochi": "kerala", "thiruvananthapuram": "kerala", "kozhikode": "kerala", "indore": "madhya pradesh",
    "bhopal": "madhya pradesh", "patna": "bihar", "ranchi": "jharkhand", "bhubaneswar": "odisha",
    "guwahati": "assam", "dehradun": "uttarakhand", "raipur": "chhattisgarh",
}

CITY_ALIASES = {
    "bombay": "mumbai", "bangalore": "bengaluru", "calcutta": "kolkata", "madras": "chennai", "gurgaon": "gurugram",
    "poona": "pune", "cochin": "kochi", "ernakulam": "kochi", "trivandrum": "thiruvananthapuram", "mysore": "mysuru",
    "baroda": "vadodara", "vizag": "visakhapatnam", "benares": "varanasi", "banaras": "varanasi",
    "mangalore": "mangaluru", "allahabad": "prayagraj", "new delhi": "delhi", "hyd": "hyderabad",
    "secunderabad": "hyderabad", "trichy": "tiruchirappalli", "calicut": "kozhikode", "bhubaneshwar": "bhubaneswar",
    "bengalooru": "bengaluru",
}

STATE_ALIASES = {
    "mh": "maharashtra", "dl": "delhi", "hr": "haryana", "up": "uttar pradesh", "ka": "karnataka", "tn": "tamil nadu",
    "ts": "telangana", "tg": "telangana", "ap": "andhra pradesh", "wb": "west bengal", "gj": "gujarat",
    "rj": "rajasthan", "ch": "chandigarh", "pb": "punjab", "kl": "kerala", "mp": "madhya pradesh", "br": "bihar",
    "jh": "jharkhand", "od": "odisha", "or": "odisha", "as": "assam", "uk": "uttarakhand", "cg": "chhattisgarh",
    "orissa": "odisha", "uttaranchal": "uttarakhand",
}
STATES = set(CITY_STATE.values()) | set(STATE_ALIASES.values())

ADDRESS_ABBR = {
    "rd": "road", "st": "street", "apts": "apartments", "apt": "apartment", "soc": "society", "ngr": "nagar",
    "opp": "opposite", "nr": "near", "col": "colony", "sec": "sector", "flr": "floor", "hno": "house number",
    "no": "number", "chs": "society", "blk": "block", "bldg": "building", "ph": "phase", "marg": "road",
}
