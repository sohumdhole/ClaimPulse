# Raw data (not committed)

The pipeline needs three files from 4TU.ResearchData (licence CC BY-NC 4.0). Put them in this folder unchanged (do not unzip the .gz).

1. **Domestic Declarations**: open https://data.4tu.nl/datasets/6a0a26d2-82d0-4018-b1cd-89afb0e8627f, scroll to DATA and click `DomesticDeclarations.xes.gz`.
   Expected size 933,930 bytes, MD5 `6a78c39491498363ce4788e0e8ca75ef`, DOI 10.4121/uuid:3f422315-ed9d-4882-891f-e180b5b4feb5.

2. **International Declarations**: open https://data.4tu.nl/articles/_/12687374/1, scroll to DATA and click `InternationalDeclarations.xes.gz`.
   Expected size 1,581,144 bytes, MD5 `1ec65e046f70bb399cc6d2c154cd615a`, DOI 10.4121/uuid:2bbf8f6a-fc50-48eb-aa9e-c4ea5ef7e8c5.

3. **Request For Payment**: open https://data.4tu.nl/articles/_/12706886/1, scroll to DATA and click `RequestForPayment.xes.gz`.
   Expected size 696,896 bytes, MD5 `2eb4dd20e70b8de4e32cc3c239bde7f2`, DOI 10.4121/uuid:895b26fb-6f25-46eb-9e48-0dca26fcd030.

If you downloaded the 'download all files (zip)' bundle instead, unzip it and copy only the `.xes.gz` file.

The ingest step checks every MD5 and stops if a file differs from the published version.
