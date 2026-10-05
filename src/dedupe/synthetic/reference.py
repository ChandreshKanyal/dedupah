"""Reference data: Indian names by community, cities with localities/PIN prefixes, product catalogues."""

COMMUNITIES = {
    "north_hindu": {
        "male": ["Rajesh", "Amit", "Vikram", "Sanjay", "Anil", "Rahul", "Manoj", "Deepak", "Suresh", "Ashok",
                 "Nitin", "Pankaj", "Vivek", "Rohit", "Arun", "Gaurav", "Mukesh", "Ravi", "Sandeep", "Yogesh"],
        "female": ["Sunita", "Anita", "Priya", "Neha", "Pooja", "Kavita", "Meena", "Rekha", "Seema", "Shweta",
                   "Ritu", "Anjali", "Nisha", "Sapna", "Geeta", "Radha", "Savita", "Poonam", "Komal", "Jyoti"],
        "surnames": ["Sharma", "Verma", "Gupta", "Singh", "Yadav", "Mishra", "Pandey", "Tiwari", "Agarwal",
                     "Jain", "Chauhan", "Saxena", "Srivastava", "Bansal", "Joshi", "Dubey", "Kapoor", "Mehta"],
        "middle": ["Kumar", "Prasad", "Chandra", "Nath", "Lal", "Mohan"],
    },
    "south_hindu": {
        "male": ["Venkatesh", "Suresh", "Ramesh", "Krishna", "Karthik", "Arun", "Srinivas", "Mahesh", "Ganesh",
                 "Balaji", "Murali", "Harish", "Prakash", "Satish", "Naveen", "Chandrasekhar", "Raghavan", "Vijay"],
        "female": ["Lakshmi", "Priya", "Divya", "Anitha", "Kavitha", "Meena", "Padma", "Sudha", "Revathi",
                   "Swathi", "Deepa", "Vani", "Latha", "Geetha", "Radhika", "Sowmya", "Bhavani", "Nandini"],
        "surnames": ["Iyer", "Iyengar", "Reddy", "Naidu", "Rao", "Nair", "Menon", "Pillai", "Subramanian",
                     "Krishnan", "Raman", "Murthy", "Hegde", "Shetty", "Gowda", "Kumar", "Natarajan", "Varma"],
        "middle": ["Kumar", "Sekhar", "Babu", "Mohan", "Raj"],
    },
    "bengali": {
        "male": ["Abhijit", "Subhas", "Sourav", "Arindam", "Partha", "Debashis", "Tapan", "Sumit", "Ranjit",
                 "Biswajit", "Soumya", "Anirban", "Pradip", "Kaushik"],
        "female": ["Sumitra", "Mousumi", "Rina", "Shreya", "Debjani", "Anindita", "Moumita", "Sharmila",
                   "Madhumita", "Paromita", "Tanushree", "Aparna"],
        "surnames": ["Banerjee", "Chatterjee", "Mukherjee", "Ghosh", "Das", "Sen", "Bose", "Dutta", "Roy",
                     "Chakraborty", "Bhattacharya", "Sarkar", "Mondal", "Saha"],
        "middle": ["Kumar", "Chandra", "Nath", "Kanti"],
    },
    "western_hindu": {
        "male": ["Nilesh", "Hitesh", "Jignesh", "Kunal", "Prashant", "Sachin", "Mahesh", "Dhaval", "Chirag",
                 "Bhavesh", "Ketan", "Swapnil", "Omkar", "Tushar", "Ajinkya"],
        "female": ["Hetal", "Bhavna", "Mansi", "Kiran", "Dipti", "Varsha", "Smita", "Rutuja", "Snehal",
                   "Pallavi", "Aarti", "Trupti", "Namrata", "Mitali"],
        "surnames": ["Patel", "Shah", "Desai", "Mehta", "Joshi", "Kulkarni", "Deshmukh", "Patil", "Jadhav",
                     "Pawar", "Shinde", "Gaikwad", "Thakkar", "Modi", "Parekh", "Bhatt", "More", "Kamble"],
        "middle": ["Kumar", "Ramesh", "Prakash", "Bhai"],
    },
    "punjabi_sikh": {
        "male": ["Harpreet", "Gurpreet", "Jaspreet", "Manpreet", "Amarjit", "Balwinder", "Kuldeep", "Surinder",
                 "Davinder", "Navdeep", "Jagdeep", "Sukhwinder", "Rajinder"],
        "female": ["Harleen", "Simran", "Manjeet", "Jasleen", "Gurleen", "Amrita", "Kamaljit", "Navneet",
                   "Paramjit", "Rupinder", "Sukhpreet"],
        "surnames": ["Singh", "Kaur", "Gill", "Sandhu", "Dhillon", "Sidhu", "Bajwa", "Grewal", "Chahal",
                     "Brar", "Randhawa", "Bedi", "Ahluwalia", "Arora", "Khanna", "Malhotra"],
        "middle": ["Singh", "Pal", "Jit"],
    },
    "muslim": {
        "male": ["Mohammed", "Imran", "Salman", "Arif", "Faisal", "Irfan", "Rashid", "Nadeem", "Shahid",
                 "Zakir", "Aamir", "Javed", "Tariq", "Farhan", "Mustafa", "Abdul"],
        "female": ["Fatima", "Ayesha", "Shabana", "Nasreen", "Farzana", "Rukhsar", "Zainab", "Shaista",
                   "Tabassum", "Sana", "Noor", "Rubina", "Mehnaz", "Gulnaz"],
        "surnames": ["Khan", "Ansari", "Sheikh", "Qureshi", "Siddiqui", "Syed", "Pathan", "Mirza", "Ali",
                     "Hussain", "Malik", "Shaikh", "Chaudhary", "Rizvi", "Ahmed"],
        "middle": ["Abdul", "Ahmed", "Ali", "Hussain"],
    },
    "christian": {
        "male": ["Joseph", "Thomas", "George", "Joseph", "Anthony", "Francis", "Paul", "Peter", "Mathew",
                 "Jacob", "Samuel", "Daniel", "Vincent", "Sebastian", "Cyril"],
        "female": ["Mary", "Susan", "Annie", "Elizabeth", "Grace", "Teresa", "Jessy", "Mercy", "Leena",
                   "Sheela", "Rosy", "Bincy", "Agnes", "Lissy"],
        "surnames": ["D'Souza", "Fernandes", "Pereira", "Rodrigues", "Menezes", "Thomas", "Mathew", "Joseph",
                     "George", "Varghese", "Abraham", "Philip", "Mascarenhas", "Gonsalves", "DCruz"],
        "middle": ["John", "Kumar", "Paul", "Mary"],
    },
}

# Name spelling variants that really occur across systems.
NAME_VARIANTS = {
    "mohammed": ["Mohd", "Muhammad", "Mohammad", "Md", "Mohamed"],
    "krishna": ["Krushna", "Krishnaa", "Kishan"],
    "venkatesh": ["Venkatesha", "Venkatesan", "Venkatesh"],
    "sharma": ["Sarma", "Sharama"],
    "srivastava": ["Shrivastava", "Srivastav", "Shrivastav"],
    "chatterjee": ["Chaterjee", "Chattopadhyay", "Chatterji"],
    "mukherjee": ["Mukerjee", "Mukhopadhyay", "Mukherji"],
    "banerjee": ["Bandopadhyay", "Banerji", "Bannerjee"],
    "agarwal": ["Aggarwal", "Agrawal", "Agarwala"],
    "gupta": ["Gupt", "Guptaa"],
    "iyer": ["Iyyer", "Aiyar", "Ayyar"],
    "reddy": ["Reddi", "Reddey"],
    "d'souza": ["Dsouza", "D Souza", "DSouza", "De Souza"],
    "fernandes": ["Fernandez", "Fernandis"],
    "chandresh": ["Chandresha", "Chandersh"],
    "rajesh": ["Rajeshwar", "Rajeev"],
    "kumar": ["Kr", "Kumaar"],
    "singh": ["Sing", "Sinha"],
    "kaur": ["Kour", "Kaurr"],
    "ahmed": ["Ahmad", "Ahamed"],
    "hussain": ["Hussein", "Husain", "Hossain"],
    "sandeep": ["Sandip", "Sundeep"],
    "deepak": ["Dipak", "Deepaak"],
    "pooja": ["Puja", "Poojaa"],
    "kavita": ["Kavitha", "Kaveeta"],
    "lakshmi": ["Laxmi", "Lakshmy", "Luxmi"],
    "gurpreet": ["Gurpreeth", "Gurprit"],
    "joshi": ["Joshee", "Josi"],
    "patil": ["Pathil", "Patill"],
}

HONORIFIC = {
    ("M", True): ["Mr", "Mr.", "Shri", "Sri"],
    ("F", True): ["Mrs", "Mrs.", "Smt", "Smt."],
    ("F", False): ["Ms", "Ms.", "Miss", "Kumari"],
}

# city, state, state code (DL), PIN prefix (3 digits), aliases, localities, community weights
CITIES = [
    {"city": "Mumbai", "state": "Maharashtra", "sc": "MH", "pin": "400", "aliases": ["Bombay", "Mumbai City"],
     "loc": ["Andheri West", "Bandra East", "Borivali", "Powai", "Thane West", "Dadar", "Malad", "Vashi", "Kandivali", "Ghatkopar"],
     "mix": {"western_hindu": 4, "north_hindu": 2, "muslim": 2, "christian": 1, "south_hindu": 1}},
    {"city": "Pune", "state": "Maharashtra", "sc": "MH", "pin": "411", "aliases": ["Poona"],
     "loc": ["Kothrud", "Hadapsar", "Baner", "Wakad", "Viman Nagar", "Aundh", "Kharadi"],
     "mix": {"western_hindu": 6, "north_hindu": 1, "muslim": 1, "south_hindu": 1}},
    {"city": "Delhi", "state": "Delhi", "sc": "DL", "pin": "110", "aliases": ["New Delhi", "Delhi NCR"],
     "loc": ["Rohini", "Dwarka", "Laxmi Nagar", "Karol Bagh", "Janakpuri", "Saket", "Pitampura", "Vasant Kunj"],
     "mix": {"north_hindu": 5, "punjabi_sikh": 2, "muslim": 2, "christian": 1}},
    {"city": "Gurugram", "state": "Haryana", "sc": "HR", "pin": "122", "aliases": ["Gurgaon"],
     "loc": ["DLF Phase 3", "Sector 56", "Sohna Road", "Sushant Lok", "Palam Vihar"],
     "mix": {"north_hindu": 5, "punjabi_sikh": 2, "south_hindu": 1}},
    {"city": "Noida", "state": "Uttar Pradesh", "sc": "UP", "pin": "201", "aliases": ["Gautam Buddh Nagar"],
     "loc": ["Sector 62", "Sector 18", "Sector 137", "Greater Noida West"],
     "mix": {"north_hindu": 6, "muslim": 1, "south_hindu": 1}},
    {"city": "Lucknow", "state": "Uttar Pradesh", "sc": "UP", "pin": "226", "aliases": ["Lakhnau"],
     "loc": ["Gomti Nagar", "Aliganj", "Indira Nagar", "Alambagh", "Hazratganj"],
     "mix": {"north_hindu": 6, "muslim": 3}},
    {"city": "Bengaluru", "state": "Karnataka", "sc": "KA", "pin": "560", "aliases": ["Bangalore", "Bengalooru"],
     "loc": ["Koramangala", "Whitefield", "Indiranagar", "HSR Layout", "Jayanagar", "Electronic City", "Marathahalli"],
     "mix": {"south_hindu": 5, "north_hindu": 1, "christian": 1, "muslim": 1}},
    {"city": "Chennai", "state": "Tamil Nadu", "sc": "TN", "pin": "600", "aliases": ["Madras"],
     "loc": ["Adyar", "T Nagar", "Anna Nagar", "Velachery", "Tambaram", "Porur"],
     "mix": {"south_hindu": 6, "christian": 1, "muslim": 1}},
    {"city": "Hyderabad", "state": "Telangana", "sc": "TS", "pin": "500", "aliases": ["Secunderabad", "Hyd"],
     "loc": ["Gachibowli", "Madhapur", "Banjara Hills", "Kukatpally", "Begumpet", "Uppal"],
     "mix": {"south_hindu": 5, "muslim": 3, "north_hindu": 1}},
    {"city": "Kolkata", "state": "West Bengal", "sc": "WB", "pin": "700", "aliases": ["Calcutta"],
     "loc": ["Salt Lake", "Behala", "Dum Dum", "Garia", "New Town", "Howrah"],
     "mix": {"bengali": 7, "north_hindu": 1, "muslim": 1}},
    {"city": "Ahmedabad", "state": "Gujarat", "sc": "GJ", "pin": "380", "aliases": ["Amdavad"],
     "loc": ["Satellite", "Navrangpura", "Maninagar", "Bopal", "Vastrapur"],
     "mix": {"western_hindu": 7, "muslim": 1}},
    {"city": "Jaipur", "state": "Rajasthan", "sc": "RJ", "pin": "302", "aliases": ["Pink City"],
     "loc": ["Malviya Nagar", "Vaishali Nagar", "Mansarovar", "C Scheme", "Jagatpura"],
     "mix": {"north_hindu": 7, "muslim": 1, "western_hindu": 1}},
    {"city": "Chandigarh", "state": "Chandigarh", "sc": "CH", "pin": "160", "aliases": ["Mohali"],
     "loc": ["Sector 17", "Sector 35", "Sector 22", "Manimajra"],
     "mix": {"punjabi_sikh": 5, "north_hindu": 3}},
    {"city": "Kochi", "state": "Kerala", "sc": "KL", "pin": "682", "aliases": ["Cochin", "Ernakulam"],
     "loc": ["Edappally", "Kakkanad", "Panampilly Nagar", "Fort Kochi", "Vyttila"],
     "mix": {"christian": 4, "south_hindu": 4, "muslim": 2}},
    {"city": "Indore", "state": "Madhya Pradesh", "sc": "MP", "pin": "452", "aliases": [],
     "loc": ["Vijay Nagar", "Palasia", "Rau", "Bhawarkua", "Saket Nagar"],
     "mix": {"north_hindu": 5, "western_hindu": 2, "muslim": 1}},
    {"city": "Patna", "state": "Bihar", "sc": "BR", "pin": "800", "aliases": ["Patliputra"],
     "loc": ["Boring Road", "Kankarbagh", "Rajendra Nagar", "Danapur"],
     "mix": {"north_hindu": 7, "muslim": 2}},
    {"city": "Nagpur", "state": "Maharashtra", "sc": "MH", "pin": "440", "aliases": [],
     "loc": ["Dharampeth", "Manish Nagar", "Sitabuldi", "Wardha Road"],
     "mix": {"western_hindu": 6, "north_hindu": 2, "muslim": 1}},
    {"city": "Coimbatore", "state": "Tamil Nadu", "sc": "TN", "pin": "641", "aliases": ["Kovai"],
     "loc": ["RS Puram", "Peelamedu", "Saibaba Colony", "Gandhipuram"],
     "mix": {"south_hindu": 8, "christian": 1}},
    {"city": "Bhopal", "state": "Madhya Pradesh", "sc": "MP", "pin": "462", "aliases": [],
     "loc": ["Arera Colony", "MP Nagar", "Kolar Road", "Berasia Road"],
     "mix": {"north_hindu": 6, "muslim": 3}},
    {"city": "Surat", "state": "Gujarat", "sc": "GJ", "pin": "395", "aliases": [],
     "loc": ["Adajan", "Vesu", "Varachha", "Piplod"],
     "mix": {"western_hindu": 8, "muslim": 1}},
]

STREET_WORDS = ["MG Road", "Gandhi Marg", "Nehru Road", "Station Road", "Temple Street", "Main Road", "Ring Road",
                "Park Street", "Church Road", "Market Road", "Lake View Road", "Hill Road", "Link Road"]
HOUSE_PREFIX = ["Flat", "House No", "Plot", "H.No", "Apt", "Door No"]
SOCIETY = ["Sai Krupa Apartments", "Green Valley Society", "Sunrise Heights", "Shanti Nagar CHS", "Lotus Residency",
           "Royal Enclave", "Ganga Vihar", "Orchid Towers", "Krishna Kunj", "Maple Gardens", "Shree Residency", None]
LANDMARKS = ["Near Bus Stand", "Opp. City Mall", "Behind Post Office", "Near Temple", "Next to SBI ATM",
             "Near Metro Station", None, None, None]

ADDRESS_ABBR = [("Road", "Rd"), ("Street", "St"), ("Apartments", "Apts"), ("Society", "Soc"), ("Nagar", "Ngr"),
                ("Opposite", "Opp"), ("Near", "Nr"), ("Colony", "Col"), ("Sector", "Sec"), ("Floor", "Flr")]

OCCUPATIONS = [
    # occupation, employment_type, employers, (min, max) annual income in INR
    ("Software Engineer", "Salaried", ["TCS", "Infosys", "Wipro", "HCL", "Accenture", "Cognizant", "Tech Mahindra"], (500000, 3500000)),
    ("Bank Officer", "Salaried", ["SBI", "HDFC Bank", "ICICI Bank", "PNB", "Bank of Baroda"], (450000, 1800000)),
    ("Teacher", "Salaried", ["Kendriya Vidyalaya", "DPS School", "St. Xavier's School", "Govt. School"], (250000, 900000)),
    ("Doctor", "Self-employed", ["Own Clinic", "Apollo Hospitals", "Fortis", "AIIMS", "Max Healthcare"], (600000, 6000000)),
    ("Shop Owner", "Self-employed", ["Own Business"], (200000, 2500000)),
    ("Business Owner", "Self-employed", ["Own Business", "Family Business"], (400000, 12000000)),
    ("Government Employee", "Salaried", ["Railways", "State Govt", "Central Govt", "PWD", "Income Tax Dept"], (300000, 1500000)),
    ("Accountant", "Salaried", ["Deloitte", "Grant Thornton", "Local CA Firm", "Reliance Industries"], (300000, 1200000)),
    ("Farmer", "Self-employed", ["Agriculture"], (120000, 800000)),
    ("Driver", "Self-employed", ["Ola", "Uber", "Own Vehicle"], (120000, 400000)),
    ("Homemaker", "Not employed", [None], (0, 0)),
    ("Retired", "Retired", [None], (100000, 600000)),
    ("Consultant", "Self-employed", ["Freelance"], (400000, 4000000)),
    ("Manager", "Salaried", ["Tata Motors", "Mahindra", "L&T", "Reliance Industries", "ITC", "Hindustan Unilever"], (700000, 4500000)),
]
EDUCATION = ["10th", "12th", "Graduate", "Post Graduate", "Professional Degree", "Diploma", "Doctorate"]
MARITAL = ["Single", "Married", "Widowed", "Divorced"]
FREE_MAIL = ["gmail.com", "yahoo.co.in", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com", "protonmail.com"]

BANK_PRODUCTS = ["Savings", "Current", "Salary", "Fixed Deposit", "Recurring Deposit", "NRI Savings", "Demat"]
BRANCH_PREFIX = ["BR", "BRN", "B"]
POLICY_TYPES = ["Term Life", "Health Individual", "Health Floater", "Motor", "ULIP", "Endowment", "Home", "Travel"]
POLICY_PRODUCTS = {
    "Term Life": ["Secure Term Plus", "Life Shield", "Term Saver"],
    "Health Individual": ["Health Guard", "Medi Care Plus", "Arogya Suraksha"],
    "Health Floater": ["Family Health Optima", "Floater Secure", "Parivar Suraksha"],
    "Motor": ["Motor Comprehensive", "Own Damage Cover", "Third Party Plus"],
    "ULIP": ["Wealth Builder ULIP", "Growth Maximiser"],
    "Endowment": ["Money Back Plan", "Bima Gold", "Jeevan Anand"],
    "Home": ["Home Protect", "Griha Raksha"],
    "Travel": ["Globe Trotter", "Travel Easy"],
}
LOAN_TYPES = ["Home Loan", "Personal Loan", "Auto Loan", "Education Loan", "Business Loan", "Gold Loan", "Loan Against Property"]
LEAD_PRODUCTS = ["Term Insurance", "Health Insurance", "Credit Card", "Home Loan", "Personal Loan", "Fixed Deposit", "Mutual Funds", "Car Insurance"]
CAMPAIGNS = ["Diwali Offer", "Tax Saver 2026", "Summer Cashback", "NRI Special", "New Year Bonanza", "Family Cover", "Pre-approved Loan"]
LEAD_SOURCES = ["Website Form", "Facebook Ads", "Google Ads", "Referral", "Walk-in", "Call Centre", "Aggregator", "WhatsApp"]
