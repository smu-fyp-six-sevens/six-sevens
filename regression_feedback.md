for the whole test  suite, we need to make sure that absolutely no external calls go to supabase or claude. if there is any conflict between A/C and code, go with what the code says and flag it out, likely the A/C is outdated.
generate a test plan with all questions answered at the top

for di-015, what is the toast it is checking? i dont think there are any toasts implemented in our frontend.

lets add in the 4 test cases that you mentioned for scrum 6 as well. good to add

i realise theres not much there for the agentic workflow. we will need regression testing. please mock up the langgraph for classification (SCRUM-8) and extraction (SCRUM-9) respectively (these were done in sprint 1). here are the A/C for classify:
As a Loan Officer, I want to classify each document and assign a confidence score so that recognised documents with sufficient confidence can proceed to analysis, while unrecognised or low-confidence documents are flagged for manual review.

Acceptance Criteria


Given a document is uploaded for processing,
When document classification is completed,
Then the system shall assign the document to the correct recognised document type and record its confidence score.

Given a document cannot be matched to any recognised document type,
When classification is completed,
Then the system shall mark the document as Unrecognised and flag it for manual review.

Given a document is classified as a recognised document type,
When its confidence score is below the defined threshold,
Then the system shall flag the document for manual review and shall not send it to analysis.

Given a document is classified as a recognised document type,
When its confidence score meets or exceeds the defined threshold,
Then the system shall mark the classification as accepted and route the document to the Analysis stage for downstream processing.

Given classification is completed,
When the classification result is generated,
Then the system shall save the document type, confidence score, and routing outcome so that the Loan Officer can view the document's current processing status.

and for extract:

As a Loan Officer, I want necessary information to be extracted from uploaded documents so that the extracted information can be used for fraud detection.

Acceptance Criteria


AC1: Given a document has been classified, when extraction runs, then the system extracts available fields that are relevant to the identified document type and saves it to the database.
AC2: Given a document's type is returned 'unrecognised' by the classifier agent, when extraction runs, then the system extracts any fields it identifies as relevant to a loan application.
AC3: Given a document is an image or scanned PDF, when extraction runs, then OCR is used to obtain machine-readable text.
AC4: For each extracted field, the system records and displays a confidence value alongside the extracted value and saves both to the database, along with an overall extraction confidence for the document.
AC5: Given a required field could not be extracted, when extraction completes, then the field is saved as null and displayed as "-" in the case view page.

Autotrigger classification (SCRUM - 17): As a Loan Officer, I want every submitted document to be validated, stored, queued, and automatically handed off to the Classifier Agent so that every successfully submitted document enters processing and remains fully traceable throughout the handoff.

Acceptance Criteria


Given a document is stored, when queuing occurs, then its status updates to queued and it enters the background task queue for async processing.

Given a document is in queue, when its dequeued, then the Classifier Agent's workflow is invoked for that document with the correct document ID and document information as mandatory inputs, within 1 second, and its status updates to processing.

Given the workflow was triggered, when invocation fails, then it is retried 3 times; if retries are exhausted, the document's status is set to failed and sent back to the user for manual review.

Given a document has been in queued or processing status longer than 10 seconds with no update, when checked, then it is queryable as stuck/at-risk i.e. no accepted document can become invisible to monitoring.

i realise we changed the stuck to 10 mins. the A/C is outdated. 


Auto trigger extraction (SCRUM-19): for this one, please also include tests where the classification is retriggered, and also, when the user confirms a doc type. (actually, scrum 17, 19 and 20 kind of test similar things. tag them according to what you think, its not too significant but as long as the logic is tested, it is ok)

As a Forgery Team Analyst, I want Classification and Extraction agents to work together i.e. Extraction agent is able to inform a second classification attempt when needed, so that a document reaches the right type wherever possible, without looping indefinitely or needing a human for cases the system can resolve itself.

Acceptance Criteria


Given Classification confidently determines a document's type, when this happens, then Extraction runs directly using that type's schema automatically.

Given Classification returns "unrecognised" or a confidence below the threshold, when this happens, then the document proceeds to Extraction using a generic/freeform schema, rather than stopping immediately.

Given generic extraction surfaces content suggesting a specific recognised type, when this occurs, then Classification is re-run once with that additional context.

Given Classification was confident but Extraction's content clearly doesn't match the claimed type, when this occurs, then Classification is re-run once with Extraction's reason, the same as AC3's re-check.

Given a re-check under AC3 or AC4 has already occurred for a document, when either condition arises again, then no further re-check is triggered i.e. the loop runs at most once.

Given a re-check is run, when classification reaches a confident, matching type, then Extraction re-runs with the corrected schema and the document proceeds normally.

Given the single retry is exhausted and the type is still unrecognised or still doesn't match the content, when this occurs, then the document is finalized as "Other" if genuinely untyped, or routed to manual review if the disagreement itself is the ambiguity.

Given any re-check occurs, when it's logged, then both the original and revised classification attempts, and which agent triggered the re-check and why, are retrievable together.


SCrum 20 (Validate Extracted Data and Support Manual Correction): As a Loan Officer, I want the system to validate extracted document fields and flag missing or invalid information for manual review, so that I can correct the information before it is passed to fraud analysis.

Acceptance Criteria


Given a document has completed extraction, when one or more required fields are missing or do not meet the expected format, then the system shall flag the document for manual review and identify the affected fields.

Given a document has been flagged for manual review, when the Loan Officer reviews the extracted information, then the Loan Officer shall be able to manually enter or update the flagged fields.

Given all required fields have been completed or corrected, when the Loan Officer submits the updated information, then the document shall be marked as ready and passed to the next analysis stage.

Given one or more required fields remain missing or invalid, when the Loan Officer attempts to submit the document, then the system shall prevent the document from proceeding to the next analysis stage and continue to flag the affected fields.

(untagged)	An unrecognised document: type dropdown starts blank/disabled-confirm, selecting "Other" and confirming persists document_type: 'unrecognised' and displays as "Other" - i do not think it starts blank? check the code on this, and if the implementation differs, follow the code. tag it as DI-others

DI-025 (test.fail, REG-003)	Known, tracked defect: a clean credit report (defaults: [], the normal/correct case) can never be approved — the check treats an empty list as "missing"
DI-025 (test.fail, REG-002)	Known, tracked defect: Save fields refuses to save if any field is empty, even one extraction was confident is legitimately absent (not just the flagged one)

i dont get these, please tell me why these are defects and if they can be fixed

yes please add add/delete custom field. 

and we also need to test the logic for *when* a field can be edited. fields (including classification) cannot be edited after approval/rejection

i agree with this:Correcting a low-confidence but recognised type — the only type-correction test (untagged, in document-review.spec.ts) covers "Unrecognised → Other." Nothing tests the more common case: a document classified as a real type but below the confidence threshold, which an officer corrects to a different real type. also, what is the current behaviour when the classification confidence is low but it is the correct doc type, on the first extraction, does it go to the generic prompt or the doc specific prompt? if its the first, then test if the extraction is rerun with the correct doc type, if second, then make sure that the doc is not reextracted. (if the code differs here, flag out)


## sprint 2

(Scrum 17, 19 and 20 were part of sprint 1, but completed in sprint 2. lets tag them as sprint 2)


SCRUM 78(View cases): yes lets add the gap test cases.

SCRUM-96 (create case): lets add a test case for 100 char limit

SCRUM-109(Classifier Agent Tools to Return Top 3 Results): As a developer, I want the Classifier agent to have tools that can return the top 3 highest confidence document types, so that the agent can better decide which document type a selected document is.

Acceptance Criteria


Each of the 3 tools should return an array of values that show the top 3 document types along with their reasoning.

The total confidence across the 3 document types should sum to 1. 

The agent should be able to understand that it has been given options and can better classify.

Proof-of-address "unmatched names" hint — UploadPage.tsx's .requirement-unmatched block (UploadPage.tsx:1720) shows names that have an address on file but don't match any identified individual (backend: compute_unmatched_proof_of_address_names). Never triggered in any spec.
NRIC "missing side" hint — the single-identity case where only the front or only the back has been uploaded shows a specific hint ("front uploaded, back missing") instead of a bare fraction (UploadPage.tsx:1685). DI-036 only tests the complete 2/2 case.

yes these 2 are crucial

in terms of other things (delete case, detection modules etc, these were developed in the current sprint, defer the e2e test cases for these to later.)
