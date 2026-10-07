# Research: what industry-leading tools do, and what we would add (2026-10-07)

Asked by the user: make the platform robust, flexible and able to take "any form of data", including paired images, ID-card
images, numbers read from cards and face matching; it should recognise data types and pick strategies by itself.
Nothing here is built yet. Every claim about a vendor comes from its public material, not from testing it.

## 1. What the leading tools do

| Tool | What stands out | What we already have | What we lack |
|---|---|---|---|
| **Senzing** | "Principles" instead of rules: each attribute has a known behaviour (a tax ID points to one person, a birth date is shared by many). Matches each record against the whole *entity*, not record against record. Finds relationships while resolving. Works for people, companies, vessels, vehicles. | ID-anchored scoring; contradiction guards; households | Attribute behaviour profiles as configuration; record-to-entity matching; entity types other than people |
| **Reltio (being bought by SAP)** | Rule-based matching plus a pre-trained model (FERN) using large language models, with no training for each customer; transliteration across 60 languages | Rules; learned weights | Pre-trained matching; Hindi/regional-script transliteration |
| **Informatica MDM** | Rules plus a supervised model that learns from data stewards' decisions | Steward decisions stored ready for training | Using those decisions to retrain |
| **Tamr** | AI-first matching with rules as guard rails; learns from experts' feedback (active learning) | Guard rails | Active learning: asking reviewers about the pairs that teach the model the most |
| **Quantexa** | Pre-trained models across many sources; resolved entities form a network graph; machine learning finds *overlinking* (wrong merges) and *underlinking* (missed merges) and their causes | Households (a small graph) | Wider relationship graph; automatic over/underlinking detection |
| **Splink / Zingg (open source)** | Fellegi-Sunter with no labels (Splink, on DuckDB like us); active learning (Zingg) | Same EM approach in `unsupervised_em` | Charts that explain the model |
| **HyperVerge, IDfy (Indian onboarding)** | Read ID cards (OCR), check for tampering, compare a selfie with the card photo, check liveness, and run **face deduplication** to stop repeat fraudsters | Nothing for images | Every image capability |

What none of them claim: deduplicating *any* data with no knowledge of what it is. They all work from a declared or detected
entity type (person, company, address) and attribute types (name, ID, phone). "Any data" in practice means recognising the
types in the data reliably, then using the strategy that fits each type. That is the target below.

## 2. Proposed additions, in phases

### Phase A: smarter tabular engine (no new legal risk, highest value)
1. **Automatic type detection.** Profile each column's values (patterns, checksums, value spread) to recognise PAN, Aadhaar,
   customer number, phone, email, dates, PIN, IFSC, account number, GSTIN, vehicle number, names and addresses. Mapping is then
   proposed from the values, not only the column names, and each type brings its own cleaning and comparison.
2. **Attribute behaviour profiles (Senzing-style).** Each type declares whether it is "one per person" (PAN), "shared by a few"
   (phone, address) or "shared by many" (birth date). The engine sets keys, vetoes and weights from these profiles, and they
   replace hand-made rules for new fields.
3. **Record-to-entity matching.** Compare a new record with the whole person built so far (all the names, phones and addresses
   seen), not only with single records.
4. **Learning from reviewers + active learning.** Retrain the weights from steward decisions, and send reviewers the pairs that
   teach the model the most.
5. **Over/underlinking detection.** Flag people whose records form suspicious shapes (for example two separate groups joined by
   one link) and values split over many people. This includes the "identifier check" already offered.
6. **Transliteration.** Compare names written in Devanagari and other Indian scripts with their Latin spellings.
7. **More entity types later.** Companies and other entities as separate entity models (today: individuals only).

### Phase B: documents and images (needs the legal gates first)
1. **Image ingest with pairing:** folders or object storage plus a manifest linking each image to its record and its kind
   (card front/back, photo). Images are encrypted at rest like everything else.
2. **Exact and near-duplicate images:** a content hash catches identical files; a perceptual hash catches resized or recompressed
   copies. Cheap on a CPU.
3. **Card type recognition:** PAN, Aadhaar, voter ID, passport, driving licence.
4. **Reading fields from cards (OCR):** PaddleOCR (Apache-2.0 licence, runs on a CPU). Read values become *evidence with a
   confidence level*, checked against the typed values; they never silently overwrite them.
5. **Aadhaar masking at the door (mandatory):** UIDAI and the RBI KYC Master Direction require the first eight digits and the QR
   code to be masked before an Aadhaar copy is stored. Only the keyed token would be kept, as today.

### Phase C: face matching (biometric; highest legal risk)
1. **Detect and compare faces** on a CPU: OpenCV YuNet (MIT licence) for detection and SFace (Apache-2.0 licence) for recognition,
   a few milliseconds per face. **Not InsightFace's ready-made models:** they are for non-commercial research only unless
   licensed.
2. **Face as one more comparison.** It adds weight to the score and guards against wrong merges, but **never merges two records
   on a face alone**. Card photos are small, old and printed, so they are weaker than live selfies.
3. **Face search index** to find similar faces quickly across millions of records (FAISS, or DuckDB's vector search).
4. **Storage rules.** Keep only encrypted face templates where policy allows. Every face comparison shown to a reviewer is logged.

### Not planned (buy, or out of scope)
- **Liveness checks and document-tampering detection:** these belong to onboarding (HyperVerge, IDfy and similar). We would accept
  their results as inputs.
- **Large-language-model matching:** too slow on a CPU at our scale, and sending data to a hosted model conflicts with the privacy
  rules.
- **Audio, video and free text** as matching inputs.

## 3. Honest constraints
- **Law.**
  - Face images are biometric personal data, and ID-card images carry Aadhaar.
  - DPDP Rules were notified on 14 Nov 2025. The main duties (consent, security, retention, breach notice) apply from
    **14 May 2027**, and banks are likely Significant Data Fiduciaries.
  - Phases B and C need a legal opinion before any real image is processed.
- **Accuracy.** All results so far are on synthetic data. For faces and OCR, we would need real (consented) test images to
  measure anything. We can generate synthetic cards, but not realistic faces of real people.
- **Machine.** The design is CPU only and runs on a single machine.
  - Face templates for 1 million images take roughly a few hours of CPU time per pass. That is acceptable for batch work and is
    done only once per image.
  - OCR is slower, so it runs only on new images.
- **Scope change.** "Individuals only" in the original brief changes if other entity types are added (Phase A7).

## Sources
- Senzing principle-based ER: https://senzing.com/principle-based-ER/ , https://senzing.com/how-entity-resolution-works-with-senzing/
- Reltio matching / FERN: https://community.reltio.com/reltio-best-practices/mdm-matching , https://erpresearch.com/erp-add-ons/mdm/reltio
- Informatica, SAP-Reltio: https://checkthat.ai/answers/what-are-the-best-golden-record-tools , https://www.rfp.wiki/vendors/reltio
- Tamr rules in AI-native MDM: https://www.tamr.com/blog/the-role-of-rules-in-ai-native-mdm
- Quantexa ER and graph: https://www.quantexa.com/platform/entity-resolution-software/ , https://community.quantexa.com/kb/articles/298-ai-at-quantexa
- Splink: https://pypi.org/project/splink ; Zingg: https://zingg.readthedocs.io/en/v0.5.0/zingg.html
- HyperVerge banks: https://hyperverge.co/in/industries/finance-verification/ ; IDfy: https://www.g2.com/products/idfy/discuss
- InsightFace licensing: https://www.insightface.ai/solutions/face-recognition-licensing
- OpenCV Zoo SFace/YuNet: https://github.com/opencv/opencv_zoo/blob/main/models/face_recognition_sface
- PaddleOCR: https://libraries.io/pypi/paddleocr
- Aadhaar masking: https://hyperverge.co/blog/rbi-revises-kyc-guidelines-mandates-aadhaar-masking-for-all-customers/
- DPDP Rules timeline: https://www.amsshardul.com/wp-content/uploads/2025/11/Regulatory-Alert-Enforcement-of-DPDP-Act-and-Notification-of-DPDP-Rules.pdf
