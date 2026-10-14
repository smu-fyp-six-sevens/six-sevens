(.venv) C:\Users\LENOVO\Documents\GitHub\six-sevens\backend>uvicorn app.main:app --reload
INFO:     Will watch for changes in these directories: ['C:\\Users\\LENOVO\\Documents\\GitHub\\six-sevens\\backend']
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [23188] using WatchFiles
2026-10-15 00:49:28,144 INFO classifier_pipeline.telemetry: OpenTelemetry disabled (OTEL_ENABLED=false) — spans are no-ops
INFO:     Started server process [32360]
INFO:     Waiting for application startup.
2026-10-15 00:49:28,152 INFO classifier_pipeline.queue: Document queue worker pool started (4 workers)
2026-10-15 00:49:28,923 INFO app.main: Database connectivity check passed at startup
2026-10-15 00:49:31,189 INFO classifier_pipeline.realtime_listener: Realtime listener subscribed to documents/cases changes
INFO:     Application startup complete.
2026-10-15 00:50:34,088 INFO perf: last 60s trace saves and other unscoped calls -> 1 supabase (GET documents x1; 767ms)
2026-10-15 00:50:43,105 INFO perf: GET /cases -> 3 supabase (GET cases x1, GET documents x1, GET latest_risk_scores x1; 1003ms), 1122ms total
INFO:     127.0.0.1:58689 - "GET /cases HTTP/1.1" 200 OK
INFO:     127.0.0.1:58689 - "OPTIONS /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:64098 - "OPTIONS /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:64098 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:58689 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:60142 - "WebSocket /ws/cases/1172d529-65ef-4be7-b098-1b2e2ff66f33?ticket=QB4WpOQnumPQDs0loHm0s86QNhjiW0D-u2y56cZjXFo" [accepted]
INFO:     connection open
2026-10-15 00:50:48,335 INFO perf: ws connect snapshot -> 3 supabase (GET cases x1, GET documents x1, GET case_analysis_runs x1; 627ms), 633ms total
INFO:     127.0.0.1:60002 - "OPTIONS /documents/upload-batch HTTP/1.1" 200 OK
2026-10-15 00:51:05,732 INFO perf: POST /documents/upload-batch -> 8 supabase (GET cases x2, POST storage x2, POST documents x2, PATCH documents x2; 2584ms), 1408ms total
INFO:     127.0.0.1:60002 - "POST /documents/upload-batch HTTP/1.1" 202 Accepted
2026-10-15 00:51:07,330 ERROR app.ingestion.metadata_extractor: Metadata extraction failed for C007_CertIncumbency.pdf: exiftool is not installed on the server that processed this document
2026-10-15 00:51:07,603 ERROR app.ingestion.metadata_extractor: Metadata extraction failed for C007_CertIncorp.pdf: exiftool is not installed on the server that processed this document
2026-10-15 00:51:08,414 INFO perf: ws refresh snapshot -> 15 supabase (GET cases x3, GET documents x3, GET extracted_fields x3, GET langgraph_trace x3, GET case_analysis_runs x3; 2516ms), 2675ms total
2026-10-15 00:51:09,835 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 704ms), 714ms total
2026-10-15 00:51:18,791 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 980ms), 990ms total
2026-10-15 00:51:20,373 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 687ms), 695ms total
2026-10-15 00:51:25,648 INFO perf: realtime snapshot -> 1 supabase (GET cases x1; 0ms), 128ms total
2026-10-15 00:51:32,206 INFO perf: realtime snapshot -> 2 supabase (GET cases x1, GET documents x1; 108ms), 204ms total
2026-10-15 00:51:32,347 INFO classifier_pipeline.langgraph.nodes: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: langgraph classification+extraction complete
2026-10-15 00:51:32,350 INFO perf: pipeline doc=82454cd5 -> 20 supabase (PATCH documents x6, GET documents x5, GET extracted_fields x3, PATCH cases x3, GET storage x1, DELETE extracted_fields x1, POST extracted_fields x1; 4763ms) | 2 claude (4145 in / 798 out tokens; cache read 1977 / write 0), 26615ms total
2026-10-15 00:51:34,091 INFO perf: last 60s realtime/ws: realtime.coalesced=7, realtime.event=18, ws.connect=1, ws.push=7
2026-10-15 00:51:34,091 INFO perf: last 60s trace saves and other unscoped calls -> 16 supabase (PATCH langgraph_trace x14, POST langgraph_trace x2; 3041ms)
2026-10-15 00:51:36,549 INFO perf: realtime snapshot -> 2 supabase (GET cases x1, GET documents x1; 137ms), 231ms total
2026-10-15 00:51:37,141 INFO classifier_pipeline.langgraph.nodes: Document 0d7efd3d-058c-4edf-ac9f-b0aaf1da659b: langgraph classification+extraction complete
2026-10-15 00:51:37,144 INFO perf: pipeline doc=0d7efd3d -> 20 supabase (PATCH documents x6, GET documents x5, GET extracted_fields x3, PATCH cases x3, GET storage x1, DELETE extracted_fields x1, POST extracted_fields x1; 5907ms) | 2 claude (5452 in / 1813 out tokens; cache read 0 / write 0), 31603ms total
2026-10-15 00:51:37,708 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 633ms), 645ms total
INFO:     127.0.0.1:62535 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b HTTP/1.1" 200 OK
INFO:     127.0.0.1:54537 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/detection/report HTTP/1.1" 200 OK
INFO:     127.0.0.1:65237 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/langgraph-trace HTTP/1.1" 200 OK
INFO:     127.0.0.1:53975 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b HTTP/1.1" 200 OK
INFO:     127.0.0.1:53890 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/detection/report HTTP/1.1" 200 OK
INFO:     127.0.0.1:62535 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/langgraph-trace HTTP/1.1" 200 OK
2026-10-15 00:51:46,250 INFO perf: GET /documents/{id}/langgraph-trace -> 1 supabase (GET langgraph_trace x1; 149ms), 177ms total
INFO:     127.0.0.1:53975 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/langgraph-trace HTTP/1.1" 200 OK
2026-10-15 00:51:46,279 INFO perf: GET /documents/{id}/detection/report -> 1 supabase (GET langgraph_trace x1; 179ms), 206ms total
INFO:     127.0.0.1:53890 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/detection/report HTTP/1.1" 404 Not Found
2026-10-15 00:51:46,433 INFO perf: GET /documents/{id}/langgraph-trace -> 1 supabase (GET langgraph_trace x1; 170ms), 179ms total
INFO:     127.0.0.1:65237 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/langgraph-trace HTTP/1.1" 200 OK
2026-10-15 00:51:46,434 INFO perf: GET /documents/{id}/detection/report -> 1 supabase (GET langgraph_trace x1; 139ms), 151ms total
INFO:     127.0.0.1:53975 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/detection/report HTTP/1.1" 404 Not Found
2026-10-15 00:51:46,563 INFO perf: GET /documents/{id} -> 3 supabase (GET extracted_fields x2, GET documents x1; 473ms), 494ms total
INFO:     127.0.0.1:62535 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b HTTP/1.1" 200 OK
2026-10-15 00:51:47,061 INFO perf: GET /documents/{id} -> 3 supabase (GET extracted_fields x2, GET documents x1; 480ms), 495ms total
INFO:     127.0.0.1:53975 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b HTTP/1.1" 200 OK
INFO:     127.0.0.1:53975 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/detection/case-match HTTP/1.1" 200 OK
2026-10-15 00:51:48,341 INFO perf: GET /documents/{id}/detection/case-match -> 7 supabase (GET documents x3, GET extracted_fields x3, GET cases x1; 1166ms), 1186ms total
INFO:     127.0.0.1:53975 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/detection/case-match HTTP/1.1" 200 OK
2026-10-15 00:52:34,097 INFO perf: last 60s realtime/ws: realtime.coalesced=1, realtime.event=4, ws.push=1
2026-10-15 00:52:34,098 INFO perf: last 60s trace saves and other unscoped calls -> 2 supabase (PATCH langgraph_trace x2; 192ms)
INFO:     127.0.0.1:55618 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/extracted-fields/8cdfc0e1-c4c7-42a9-9249-183accd4900d HTTP/1.1" 200 OK
2026-10-15 00:54:08,827 INFO perf: DELETE /documents/{id}/extracted-fields/{id} -> 12 supabase (GET extracted_fields x6, GET documents x3, DELETE extracted_fields x1, PATCH documents x1, PATCH cases x1; 2470ms), 2499ms total
INFO:     127.0.0.1:55618 - "DELETE /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/extracted-fields/8cdfc0e1-c4c7-42a9-9249-183accd4900d HTTP/1.1" 200 OK
2026-10-15 00:54:10,022 INFO perf: GET /documents/{id}/detection/case-match -> 7 supabase (GET documents x3, GET extracted_fields x3, GET cases x1; 1155ms), 1176ms total
INFO:     127.0.0.1:55618 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/detection/case-match HTTP/1.1" 200 OK
INFO:     127.0.0.1:55618 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/extracted-fields/4408fbe1-deba-419f-b0bc-4023ec89142a HTTP/1.1" 200 OK
2026-10-15 00:54:15,003 INFO perf: DELETE /documents/{id}/extracted-fields/{id} -> 12 supabase (GET extracted_fields x6, GET documents x3, DELETE extracted_fields x1, PATCH documents x1, PATCH cases x1; 1876ms), 1910ms total
INFO:     127.0.0.1:55618 - "DELETE /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/extracted-fields/4408fbe1-deba-419f-b0bc-4023ec89142a HTTP/1.1" 200 OK
2026-10-15 00:54:16,406 INFO perf: GET /documents/{id}/detection/case-match -> 7 supabase (GET documents x3, GET extracted_fields x3, GET cases x1; 1370ms), 1386ms total
INFO:     127.0.0.1:55618 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/detection/case-match HTTP/1.1" 200 OK
INFO:     127.0.0.1:55618 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/extracted-fields/854b3733-20fc-46e2-bb70-1796d0765e94 HTTP/1.1" 200 OK
2026-10-15 00:54:20,763 INFO perf: DELETE /documents/{id}/extracted-fields/{id} -> 12 supabase (GET extracted_fields x6, GET documents x3, DELETE extracted_fields x1, PATCH documents x1, PATCH cases x1; 1695ms), 1721ms total
INFO:     127.0.0.1:55618 - "DELETE /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/extracted-fields/854b3733-20fc-46e2-bb70-1796d0765e94 HTTP/1.1" 200 OK
2026-10-15 00:54:21,972 INFO perf: GET /documents/{id}/detection/case-match -> 7 supabase (GET documents x3, GET extracted_fields x3, GET cases x1; 1181ms), 1196ms total
INFO:     127.0.0.1:55618 - "GET /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/detection/case-match HTTP/1.1" 200 OK
2026-10-15 00:54:34,120 INFO perf: last 60s realtime/ws: realtime.event=6, realtime.no_viewer=3
INFO:     127.0.0.1:57253 - "OPTIONS /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/reject HTTP/1.1" 200 OK
2026-10-15 00:54:40,895 INFO perf: POST /documents/{id}/reject -> 9 supabase (GET extracted_fields x4, GET documents x3, PATCH documents x1, PATCH cases x1; 1629ms), 1653ms total
INFO:     127.0.0.1:57253 - "POST /documents/0d7efd3d-058c-4edf-ac9f-b0aaf1da659b/reject HTTP/1.1" 200 OK
INFO:     127.0.0.1:57253 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:64979 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:60980 - "WebSocket /ws/cases/1172d529-65ef-4be7-b098-1b2e2ff66f33?ticket=iOD3mPrp-sMUW03V6DcwqSga08klQdmUQQnF18WtU0A" [accepted]
INFO:     connection open
2026-10-15 00:54:44,981 INFO perf: ws connect snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 644ms), 652ms total
INFO:     127.0.0.1:62010 - "OPTIONS /documents/bulk-delete HTTP/1.1" 200 OK
2026-10-15 00:54:54,388 INFO perf: pushed snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 993ms), 1009ms total
2026-10-15 00:54:54,389 INFO perf: POST /documents/bulk-delete -> 7 supabase (GET documents x2, DELETE extracted_fields x1, DELETE storage x1, DELETE documents x1, GET extracted_fields x1, PATCH cases x1; 2289ms), 4015ms total
INFO:     127.0.0.1:62010 - "POST /documents/bulk-delete HTTP/1.1" 200 OK
2026-10-15 00:54:55,110 INFO perf: ws refresh snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 707ms), 714ms total
2026-10-15 00:55:34,146 INFO perf: last 60s realtime/ws: realtime.event=4, realtime.no_viewer=1, ws.connect=1, ws.push=1
2026-10-15 00:56:05,649 INFO perf: POST /documents/upload-batch -> 4 supabase (GET cases x1, POST storage x1, POST documents x1, PATCH documents x1; 1159ms), 1173ms total
INFO:     127.0.0.1:57929 - "POST /documents/upload-batch HTTP/1.1" 202 Accepted
2026-10-15 00:56:07,721 ERROR app.ingestion.metadata_extractor: Metadata extraction failed for C007_NOA.pdf: exiftool is not installed on the server that processed this document
2026-10-15 00:56:07,953 INFO perf: ws refresh snapshot -> 15 supabase (GET cases x3, GET documents x3, GET extracted_fields x3, GET langgraph_trace x3, GET case_analysis_runs x3; 2272ms), 2298ms total
2026-10-15 00:56:09,313 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 736ms), 747ms total
2026-10-15 00:56:18,635 INFO perf: realtime snapshot -> 2 supabase (GET cases x1, GET documents x1; 126ms), 234ms total
2026-10-15 00:56:19,821 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 666ms), 676ms total
2026-10-15 00:56:31,406 INFO perf: realtime snapshot -> 2 supabase (GET cases x1, GET documents x1; 122ms), 163ms total
2026-10-15 00:56:31,564 INFO classifier_pipeline.langgraph.nodes: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: langgraph classification+extraction complete
2026-10-15 00:56:31,566 INFO perf: pipeline doc=50a6f8fb -> 20 supabase (PATCH documents x6, GET documents x5, GET extracted_fields x3, PATCH cases x3, GET storage x1, DELETE extracted_fields x1, POST extracted_fields x1; 4842ms) | 2 claude (4661 in / 1465 out tokens; cache read 1977 / write 0), 25916ms total
2026-10-15 00:56:34,169 INFO perf: last 60s realtime/ws: realtime.coalesced=3, realtime.event=11, ws.push=5
2026-10-15 00:56:34,169 INFO perf: last 60s trace saves and other unscoped calls -> 9 supabase (PATCH langgraph_trace x8, POST langgraph_trace x1; 1549ms)
INFO:     127.0.0.1:56308 - "OPTIONS /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8 HTTP/1.1" 200 OK
INFO:     127.0.0.1:63875 - "OPTIONS /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/detection/report HTTP/1.1" 200 OK
INFO:     127.0.0.1:63534 - "OPTIONS /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/langgraph-trace HTTP/1.1" 200 OK
INFO:     127.0.0.1:59842 - "OPTIONS /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8 HTTP/1.1" 200 OK
INFO:     127.0.0.1:61843 - "OPTIONS /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/detection/report HTTP/1.1" 200 OK
INFO:     127.0.0.1:56308 - "OPTIONS /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/langgraph-trace HTTP/1.1" 200 OK
2026-10-15 00:56:35,379 INFO perf: GET /documents/{id}/detection/report -> 1 supabase (GET langgraph_trace x1; 149ms), 156ms total
INFO:     127.0.0.1:61843 - "GET /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/detection/report HTTP/1.1" 404 Not Found
2026-10-15 00:56:35,393 INFO perf: GET /documents/{id}/langgraph-trace -> 1 supabase (GET langgraph_trace x1; 159ms), 165ms total
INFO:     127.0.0.1:59842 - "GET /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/langgraph-trace HTTP/1.1" 200 OK
2026-10-15 00:56:35,524 INFO perf: GET /documents/{id}/detection/report -> 1 supabase (GET langgraph_trace x1; 135ms), 142ms total
INFO:     127.0.0.1:63534 - "GET /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/detection/report HTTP/1.1" 404 Not Found
2026-10-15 00:56:35,535 INFO perf: GET /documents/{id}/langgraph-trace -> 1 supabase (GET langgraph_trace x1; 132ms), 139ms total
INFO:     127.0.0.1:61843 - "GET /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/langgraph-trace HTTP/1.1" 200 OK
2026-10-15 00:56:35,603 INFO perf: GET /documents/{id} -> 3 supabase (GET extracted_fields x2, GET documents x1; 378ms), 389ms total
INFO:     127.0.0.1:56308 - "GET /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8 HTTP/1.1" 200 OK
2026-10-15 00:56:35,977 INFO perf: GET /documents/{id} -> 3 supabase (GET extracted_fields x2, GET documents x1; 360ms), 371ms total
INFO:     127.0.0.1:61843 - "GET /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8 HTTP/1.1" 200 OK
INFO:     127.0.0.1:61843 - "OPTIONS /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/detection/case-match HTTP/1.1" 200 OK
2026-10-15 00:56:37,027 INFO perf: GET /documents/{id}/detection/case-match -> 7 supabase (GET documents x3, GET extracted_fields x3, GET cases x1; 1000ms), 1013ms total
INFO:     127.0.0.1:61843 - "GET /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/detection/case-match HTTP/1.1" 200 OK
INFO:     127.0.0.1:49276 - "OPTIONS /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/extracted-fields HTTP/1.1" 200 OK
2026-10-15 00:57:13,837 INFO perf: PATCH /documents/{id}/extracted-fields -> 12 supabase (GET extracted_fields x5, GET documents x3, DELETE extracted_fields x1, POST extracted_fields x1, PATCH documents x1, PATCH cases x1; 1976ms), 2022ms total
INFO:     127.0.0.1:49276 - "PATCH /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/extracted-fields HTTP/1.1" 200 OK
2026-10-15 00:57:14,884 INFO perf: GET /documents/{id}/detection/case-match -> 7 supabase (GET documents x3, GET extracted_fields x3, GET cases x1; 1013ms), 1034ms total
INFO:     127.0.0.1:49276 - "GET /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/detection/case-match HTTP/1.1" 200 OK
INFO:     127.0.0.1:49276 - "OPTIONS /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/approve HTTP/1.1" 200 OK
2026-10-15 00:57:17,647 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: detection run starting
2026-10-15 00:57:18,035 INFO perf: POST /documents/{id}/approve -> 10 supabase (GET extracted_fields x5, GET documents x3, PATCH documents x1, PATCH cases x1; 1598ms), 1627ms total
INFO:     127.0.0.1:49276 - "POST /documents/50a6f8fb-9865-4592-b77e-e51a60201ff8/approve HTTP/1.1" 200 OK
INFO:     127.0.0.1:49276 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:64069 - "WebSocket /ws/cases/1172d529-65ef-4be7-b098-1b2e2ff66f33?ticket=TAwKr2BIQFZftLVYcJMXI1EQTJ2WBF88AcUJMy1gqQQ" [accepted]
INFO:     connection open
INFO:     127.0.0.1:49276 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:55161 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:60188 - "WebSocket /ws/cases/1172d529-65ef-4be7-b098-1b2e2ff66f33?ticket=W9THieXyw-t22wN0LVtJE6NVH5vtla4CBAuUouM3Ge4" [accepted]
INFO:     connection open
2026-10-15 00:57:20,233 INFO perf: ws connect snapshot -> 15 supabase (GET cases x3, GET documents x3, GET extracted_fields x3, GET langgraph_trace x3, GET case_analysis_runs x3; 2142ms), 2176ms total
2026-10-15 00:57:23,736 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: lead plan — briefs written for all specialists
2026-10-15 00:57:23,743 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: layout_rendering specialist starting
2026-10-15 00:57:23,753 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: content_validation specialist starting
2026-10-15 00:57:23,771 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: digital_authenticity specialist starting
INFO:     127.0.0.1:55161 - "OPTIONS /documents/82454cd5-95e9-49fc-be6c-4168cae63c19/approve HTTP/1.1" 200 OK
2026-10-15 00:57:24,856 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: detection run starting
2026-10-15 00:57:25,248 INFO perf: realtime snapshot -> 2 supabase (GET cases x1, GET documents x1; 142ms), 158ms total
2026-10-15 00:57:25,331 INFO perf: POST /documents/{id}/approve -> 10 supabase (GET extracted_fields x5, GET documents x3, PATCH documents x1, PATCH cases x1; 1481ms), 1505ms total
INFO:     127.0.0.1:55161 - "POST /documents/82454cd5-95e9-49fc-be6c-4168cae63c19/approve HTTP/1.1" 200 OK
2026-10-15 00:57:25,598 WARNING fraud_detection.ai_generation: ExifTool unavailable for AI provenance: [WinError 2] The system cannot find the file specified
2026-10-15 00:57:25,600 INFO fraud_detection.ai_generation: ai_generation document=50a6f8fb-9865-4592-b77e-e51a60201ff8 image=file indicator='Provenance' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='ExifTool is not available to read provenance.'
2026-10-15 00:57:25,723 INFO fraud_detection.ai_generation: ai_generation document=50a6f8fb-9865-4592-b77e-e51a60201ff8 image=page1/xref13 indicator='Spectral Peaks' assessed=False value=None strength=0.000 weight=0.35 contribution=0.0000 reason="Graphic rather than a photograph or scan (92% flat pixels, 142 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:25,723 INFO fraud_detection.ai_generation: ai_generation document=50a6f8fb-9865-4592-b77e-e51a60201ff8 image=page1/xref13 indicator='Noise Level' assessed=False value=None strength=0.000 weight=0.30 contribution=0.0000 reason="Graphic rather than a photograph or scan (92% flat pixels, 142 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:25,723 INFO fraud_detection.ai_generation: ai_generation document=50a6f8fb-9865-4592-b77e-e51a60201ff8 image=page1/xref13 indicator='Noise Signal Dependence' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason="Graphic rather than a photograph or scan (92% flat pixels, 142 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:25,723 INFO fraud_detection.ai_generation: ai_generation document=50a6f8fb-9865-4592-b77e-e51a60201ff8 image=page1/xref13 indicator='Error Level Analysis' assessed=False value=None strength=0.000 weight=0.10 contribution=0.0000 reason="Graphic rather than a photograph or scan (92% flat pixels, 142 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:25,723 INFO fraud_detection.ai_generation: ai_generation document=50a6f8fb-9865-4592-b77e-e51a60201ff8 image=page1/xref13 indicator='Double Compression' assessed=False value=None strength=0.000 weight=0.10 contribution=0.0000 reason="Graphic rather than a photograph or scan (92% flat pixels, 142 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:25,723 INFO fraud_detection.ai_generation: ai_generation document=50a6f8fb-9865-4592-b77e-e51a60201ff8 image=page1/xref13 indicator='Capture Signature' assessed=False value=None strength=0.000 weight=0.15 contribution=0.0000 reason="Graphic rather than a photograph or scan (92% flat pixels, 142 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:25,724 INFO fraud_detection.ai_generation: ai_generation document=50a6f8fb-9865-4592-b77e-e51a60201ff8 image=page1/xref13 indicator='Provenance' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='ExifTool is not available to read provenance.'
2026-10-15 00:57:25,724 INFO fraud_detection.ai_generation: ai_generation document=50a6f8fb-9865-4592-b77e-e51a60201ff8 image=page1/xref13 score=0.000 coverage=0.00 photographic=False
Consider using the pymupdf_layout package for a greatly improved page layout analysis.
2026-10-15 00:57:26,045 ERROR app.ingestion.metadata_extractor: Metadata extraction failed for C007_NOA.pdf: exiftool is not installed on the server that processed this document
2026-10-15 00:57:29,935 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: digital_authenticity specialist done — 0 finding(s), tools called: ['metadata_tool', 'ai_generation_tool']
2026-10-15 00:57:30,773 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: lead plan — briefs written for all specialists
2026-10-15 00:57:30,779 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: layout_rendering specialist starting
2026-10-15 00:57:30,781 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: content_validation specialist starting
2026-10-15 00:57:30,782 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: digital_authenticity specialist starting
2026-10-15 00:57:32,700 WARNING fraud_detection.ai_generation: ExifTool unavailable for AI provenance: [WinError 2] The system cannot find the file specified
2026-10-15 00:57:32,703 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=file indicator='Provenance' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='ExifTool is not available to read provenance.'
2026-10-15 00:57:32,729 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Spectral Peaks' assessed=False value=None strength=0.000 weight=0.35 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:32,730 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Noise Level' assessed=False value=None strength=0.000 weight=0.30 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:32,730 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Noise Signal Dependence' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:32,731 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Error Level Analysis' assessed=False value=None strength=0.000 weight=0.10 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:32,731 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Double Compression' assessed=False value=None strength=0.000 weight=0.10 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:32,733 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Capture Signature' assessed=False value=None strength=0.000 weight=0.15 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:32,733 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Provenance' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='ExifTool is not available to read provenance.'
2026-10-15 00:57:32,733 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 score=0.000 coverage=0.00 photographic=False
2026-10-15 00:57:32,749 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Spectral Peaks' assessed=False value=None strength=0.000 weight=0.35 contribution=0.0000 reason='Image is smaller than one 256px analysis tile.'
2026-10-15 00:57:32,750 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Noise Level' assessed=False value=None strength=0.000 weight=0.30 contribution=0.0000 reason='Only 0 smooth, unclipped 32px block(s); 8 are needed to measure noise apart from edges.'
2026-10-15 00:57:32,750 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Noise Signal Dependence' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='Only 0 smooth, unclipped 32px block(s); 8 are needed to measure noise apart from edges.'
2026-10-15 00:57:32,750 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Error Level Analysis' assessed=True value=1.19 strength=0.000 weight=0.10 contribution=0.0000
2026-10-15 00:57:32,750 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Double Compression' assessed=False value=None strength=0.000 weight=0.10 contribution=0.0000 reason='PNG carries no JPEG compression history.'
2026-10-15 00:57:32,750 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Capture Signature' assessed=False value=None strength=0.000 weight=0.15 contribution=0.0000 reason="PDF creation normally strips a camera's signature from embedded images."
2026-10-15 00:57:32,750 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Provenance' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='ExifTool is not available to read provenance.'
2026-10-15 00:57:32,751 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 score=0.000 coverage=0.10 photographic=True
2026-10-15 00:57:32,751 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 score=0.000 images=2
2026-10-15 00:57:33,070 ERROR app.ingestion.metadata_extractor: Metadata extraction failed for C007_CertIncorp.pdf: exiftool is not installed on the server that processed this document
2026-10-15 00:57:34,176 INFO perf: last 60s realtime/ws: realtime.coalesced=1, realtime.event=8, realtime.no_viewer=2, ws.connect=2, ws.push=2
2026-10-15 00:57:34,177 INFO perf: last 60s trace saves and other unscoped calls -> 5 supabase (PATCH langgraph_trace x5; 1104ms)
2026-10-15 00:57:34,951 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: layout_rendering specialist done — 3 finding(s), tools called: ['font_consistency_tool', 'layout_anomalies_tool', 'image_manipulation_tool', 'image_tampering_tool']
2026-10-15 00:57:35,865 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: content_validation specialist done — 1 finding(s), tools called: ['spelling_tool', 'date_consistency_tool', 'numerical_checks_tool', 'external_validation_tool']
2026-10-15 00:57:36,161 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: digital_authenticity specialist done — 0 finding(s), tools called: ['metadata_tool', 'ai_generation_tool']
2026-10-15 00:57:36,445 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: content_validation specialist done — 1 finding(s), tools called: ['spelling_tool', 'date_consistency_tool', 'numerical_checks_tool', 'external_validation_tool']
2026-10-15 00:57:37,682 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: layout_rendering specialist done — 2 finding(s), tools called: ['font_consistency_tool', 'layout_anomalies_tool', 'image_manipulation_tool', 'image_tampering_tool']
2026-10-15 00:57:44,082 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: lead review done — requesting a second look from content_validation
2026-10-15 00:57:44,090 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: content_validation specialist starting (second look)
2026-10-15 00:57:46,273 ERROR app.ingestion.metadata_extractor: Metadata extraction failed for C007_NOA.pdf: exiftool is not installed on the server that processed this document
2026-10-15 00:57:52,796 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: lead review done — requesting a second look from digital_authenticity
2026-10-15 00:57:52,797 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: digital_authenticity specialist starting (second look)
2026-10-15 00:57:52,993 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: content_validation specialist done — 1 finding(s), tools called: ['spelling_tool', 'date_consistency_tool', 'numerical_checks_tool', 'external_validation_tool']
2026-10-15 00:57:54,103 WARNING fraud_detection.ai_generation: ExifTool unavailable for AI provenance: [WinError 2] The system cannot find the file specified
2026-10-15 00:57:54,106 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=file indicator='Provenance' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='ExifTool is not available to read provenance.'
2026-10-15 00:57:54,116 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Spectral Peaks' assessed=False value=None strength=0.000 weight=0.35 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:54,116 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Noise Level' assessed=False value=None strength=0.000 weight=0.30 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:54,117 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Noise Signal Dependence' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:54,117 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Error Level Analysis' assessed=False value=None strength=0.000 weight=0.10 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:54,117 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Double Compression' assessed=False value=None strength=0.000 weight=0.10 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:54,117 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Capture Signature' assessed=False value=None strength=0.000 weight=0.15 contribution=0.0000 reason="Graphic rather than a photograph or scan (77% flat pixels, 6478 distinct colours); pixel statistics don't apply."
2026-10-15 00:57:54,117 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 indicator='Provenance' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='ExifTool is not available to read provenance.'
2026-10-15 00:57:54,117 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref3 score=0.000 coverage=0.00 photographic=False
2026-10-15 00:57:54,129 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Spectral Peaks' assessed=False value=None strength=0.000 weight=0.35 contribution=0.0000 reason='Image is smaller than one 256px analysis tile.'
2026-10-15 00:57:54,130 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Noise Level' assessed=False value=None strength=0.000 weight=0.30 contribution=0.0000 reason='Only 0 smooth, unclipped 32px block(s); 8 are needed to measure noise apart from edges.'
2026-10-15 00:57:54,130 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Noise Signal Dependence' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='Only 0 smooth, unclipped 32px block(s); 8 are needed to measure noise apart from edges.'
2026-10-15 00:57:54,131 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Error Level Analysis' assessed=True value=1.19 strength=0.000 weight=0.10 contribution=0.0000
2026-10-15 00:57:54,131 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Double Compression' assessed=False value=None strength=0.000 weight=0.10 contribution=0.0000 reason='PNG carries no JPEG compression history.'
2026-10-15 00:57:54,131 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Capture Signature' assessed=False value=None strength=0.000 weight=0.15 contribution=0.0000 reason="PDF creation normally strips a camera's signature from embedded images."
2026-10-15 00:57:54,131 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 indicator='Provenance' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='ExifTool is not available to read provenance.'
2026-10-15 00:57:54,131 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 image=page1/xref5 score=0.000 coverage=0.10 photographic=True
2026-10-15 00:57:54,131 INFO fraud_detection.ai_generation: ai_generation document=82454cd5-95e9-49fc-be6c-4168cae63c19 score=0.000 images=2
2026-10-15 00:57:59,774 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: digital_authenticity specialist done — 0 finding(s), tools called: ['metadata_tool', 'ai_generation_tool']
2026-10-15 00:58:00,308 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: lead review done
2026-10-15 00:58:01,029 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: detection done — verdict flagged, 4 finding(s)
2026-10-15 00:58:01,370 INFO classifier_pipeline.langgraph.detection: Document 50a6f8fb-9865-4592-b77e-e51a60201ff8: 4 flag(s) stored
2026-10-15 00:58:01,464 INFO perf: detection doc=50a6f8fb -> 12 supabase (GET documents x3, GET extracted_fields x3, PATCH documents x2, GET storage x1, GET cases x1, DELETE flags x1, POST flags x1; 2325ms) | 11 claude (26601 in / 3008 out tokens; cache read 0 / write 0), 43813ms total
2026-10-15 00:58:02,136 INFO perf: pushed snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 659ms), 671ms total
2026-10-15 00:58:03,256 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 691ms), 705ms total
2026-10-15 00:58:04,947 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: lead review done
2026-10-15 00:58:05,538 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: detection done — verdict flagged, 3 finding(s)
2026-10-15 00:58:06,084 INFO classifier_pipeline.langgraph.detection: Document 82454cd5-95e9-49fc-be6c-4168cae63c19: 3 flag(s) stored
2026-10-15 00:58:06,221 INFO perf: detection doc=82454cd5 -> 12 supabase (GET documents x3, GET extracted_fields x3, PATCH documents x2, GET storage x1, GET cases x1, DELETE flags x1, POST flags x1; 2241ms) | 11 claude (21731 in / 2321 out tokens; cache read 0 / write 0), 41362ms total
2026-10-15 00:58:06,974 INFO perf: pushed snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 734ms), 753ms total
2026-10-15 00:58:07,891 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 647ms), 665ms total
INFO:     127.0.0.1:55742 - "OPTIONS /cases/1172d529-65ef-4be7-b098-1b2e2ff66f33/analysis/run HTTP/1.1" 200 OK
2026-10-15 00:58:25,838 INFO perf: POST /cases/{id}/analysis/run -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, POST case_analysis_runs x1; 866ms), 1105ms total
INFO:     127.0.0.1:55742 - "POST /cases/1172d529-65ef-4be7-b098-1b2e2ff66f33/analysis/run HTTP/1.1" 202 Accepted
INFO:     127.0.0.1:53356 - "OPTIONS /cases/1172d529-65ef-4be7-b098-1b2e2ff66f33/analysis HTTP/1.1" 200 OK
2026-10-15 00:58:34,187 INFO perf: last 60s realtime/ws: realtime.event=2, ws.push=4
2026-10-15 00:58:34,187 INFO perf: last 60s trace saves and other unscoped calls -> 11 supabase (PATCH langgraph_trace x11; 1811ms)
2026-10-15 00:58:34,860 INFO perf: GET /cases/{id}/analysis -> 5 supabase (GET cases x1, GET case_analysis_runs x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1; 1022ms), 1047ms total
INFO:     127.0.0.1:53356 - "GET /cases/1172d529-65ef-4be7-b098-1b2e2ff66f33/analysis HTTP/1.1" 200 OK
2026-10-15 00:58:56,025 INFO perf: pushed snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 770ms), 791ms total
2026-10-15 00:58:56,025 INFO perf: case analysis case=1172d529 -> 3 supabase (GET case_analysis_runs x1, PATCH case_analysis_runs x1, POST risk_scores x1; 971ms) | 4 claude (9534 in / 1744 out tokens; cache read 1606 / write 0), 30383ms total
2026-10-15 00:58:56,806 INFO perf: GET /cases/{id}/analysis -> 5 supabase (GET cases x1, GET case_analysis_runs x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1; 736ms), 766ms total
INFO:     127.0.0.1:61020 - "GET /cases/1172d529-65ef-4be7-b098-1b2e2ff66f33/analysis HTTP/1.1" 200 OK
2026-10-15 00:59:34,209 INFO perf: last 60s realtime/ws: ws.push=1
2026-10-15 00:59:34,209 INFO perf: last 60s trace saves and other unscoped calls -> 2 supabase (PATCH case_analysis_runs x2; 625ms)
