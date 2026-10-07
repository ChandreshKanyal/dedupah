"""The canonical customer schema. Every source is mapped onto this; everything downstream sees only this."""

CANONICAL_FIELDS: dict[str, str] = {
    "full_name": "Person's name as written in the source",
    "father_name": "Father's (or husband's) name",
    "dob": "Date of birth, raw text",
    "gender": "Gender, raw text",
    "marital_status": "Marital status",
    "mobile": "Primary mobile/phone number",
    "mobile_alt": "Secondary phone number",
    "email": "Email address",
    "address": "Address as a single text line (structured parts below if the source has them)",
    "address_alt": "A second address (permanent / previous)",
    "city": "City", "state": "State", "pincode": "PIN code",
    "customer_number": "Customer number shared across your systems (CIF, UCIC, client id): the same number means the same customer. "
                       "Not a row's own id within one file",
    "pan": "PAN (permanent account number)",
    "aadhaar": "Full Aadhaar number if the source holds it (will be tokenised, never stored raw downstream)",
    "aadhaar_last4": "Last four digits of Aadhaar",
    "aadhaar_reference_key": "The reference key (token) from the organisation's own Aadhaar Data Vault; preferred over the full number, which then never needs to enter this system",
    "passport_no": "Passport number",
    "dl_no": "Driving licence number",
    "voter_id": "Voter ID (EPIC) number",
    "occupation": "Occupation",
    "annual_income": "Annual income",
    "related_name": "Another person named on the record (nominee, co-applicant): evidence for family links",
    "party_role": "Role on the record (proposer, insured, nominee...)",
    "relation_to_proposer": "Relationship stated to the primary party",
    "age": "Age or age band as given when there is no date of birth ('35', '50-59', '60+')",
    "record_date": "Date the record was created or last updated (used to turn an age into a birth year)",
}

FIXED_COLUMNS = ["source_id", "source_record_id"]
OUTPUT_COLUMNS = FIXED_COLUMNS + list(CANONICAL_FIELDS) + ["attributes"]
