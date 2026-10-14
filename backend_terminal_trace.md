(.venv) C:\Users\LENOVO\Documents\GitHub\six-sevens\backend>uvicorn app.main:app --reload
INFO:     Will watch for changes in these directories: ['C:\\Users\\LENOVO\\Documents\\GitHub\\six-sevens\\backend']
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [62016] using WatchFiles
2026-10-15 00:04:50,765 INFO classifier_pipeline.telemetry: OpenTelemetry disabled (OTEL_ENABLED=false) — spans are no-ops
INFO:     Started server process [26084]
INFO:     Waiting for application startup.
2026-10-15 00:04:50,770 INFO classifier_pipeline.queue: Document queue worker pool started (4 workers)
2026-10-15 00:04:51,176 INFO app.main: Database connectivity check passed at startup
2026-10-15 00:04:52,227 INFO classifier_pipeline.realtime_listener: Realtime listener subscribed to documents/cases changes
INFO:     Application startup complete.
INFO:     127.0.0.1:52415 - "OPTIONS /cases HTTP/1.1" 200 OK
INFO:     127.0.0.1:59778 - "OPTIONS /cases HTTP/1.1" 200 OK
2026-10-15 00:05:23,799 INFO perf: GET /cases -> 3 supabase (GET cases x1, GET documents x1, GET latest_risk_scores x1; 1413ms), 1719ms total
INFO:     127.0.0.1:59778 - "GET /cases HTTP/1.1" 200 OK
INFO:     127.0.0.1:56370 - "OPTIONS /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:62942 - "OPTIONS /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:62942 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:56370 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:49671 - "WebSocket /ws/cases/019a16e6-1f14-41d0-b56d-825e29eebf41?ticket=5JqheeVjtbaYVLmZ8EsGgcNnzqPHzBEihSeoBwIKYnA" [accepted]
INFO:     connection open
2026-10-15 00:05:38,289 INFO perf: ws connect snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 1545ms), 1579ms total
INFO:     127.0.0.1:56370 - "OPTIONS /cases/019a16e6-1f14-41d0-b56d-825e29eebf41/analysis HTTP/1.1" 200 OK
2026-10-15 00:05:39,447 INFO perf: GET /cases/{id}/analysis -> 5 supabase (GET cases x1, GET case_analysis_runs x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1; 889ms), 919ms total
INFO:     127.0.0.1:56370 - "GET /cases/019a16e6-1f14-41d0-b56d-825e29eebf41/analysis HTTP/1.1" 200 OK
INFO:     127.0.0.1:56370 - "OPTIONS /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba HTTP/1.1" 200 OK
INFO:     127.0.0.1:63750 - "OPTIONS /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/detection/report HTTP/1.1" 200 OK
INFO:     127.0.0.1:62452 - "OPTIONS /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/langgraph-trace HTTP/1.1" 200 OK
INFO:     127.0.0.1:56370 - "OPTIONS /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/detection/report HTTP/1.1" 200 OK
INFO:     127.0.0.1:57495 - "OPTIONS /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba HTTP/1.1" 200 OK
INFO:     127.0.0.1:64350 - "OPTIONS /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/langgraph-trace HTTP/1.1" 200 OK
2026-10-15 00:05:44,124 INFO perf: GET /documents/{id}/langgraph-trace -> 1 supabase (GET langgraph_trace x1; 483ms), 507ms total
INFO:     127.0.0.1:57495 - "GET /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/langgraph-trace HTTP/1.1" 200 OK
2026-10-15 00:05:44,137 INFO perf: GET /documents/{id}/detection/report -> 1 supabase (GET langgraph_trace x1; 489ms), 524ms total
INFO:     127.0.0.1:64350 - "GET /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/detection/report HTTP/1.1" 404 Not Found
2026-10-15 00:05:44,263 INFO perf: GET /documents/{id}/langgraph-trace -> 1 supabase (GET langgraph_trace x1; 126ms), 135ms total
INFO:     127.0.0.1:62452 - "GET /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/langgraph-trace HTTP/1.1" 200 OK
2026-10-15 00:05:44,300 INFO perf: GET /documents/{id} -> 3 supabase (GET extracted_fields x2, GET documents x1; 664ms), 694ms total
INFO:     127.0.0.1:56370 - "GET /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba HTTP/1.1" 200 OK
2026-10-15 00:05:44,531 INFO perf: GET /documents/{id}/detection/report -> 1 supabase (GET langgraph_trace x1; 382ms), 391ms total
INFO:     127.0.0.1:57495 - "GET /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/detection/report HTTP/1.1" 404 Not Found
2026-10-15 00:05:44,833 INFO perf: GET /documents/{id} -> 3 supabase (GET extracted_fields x2, GET documents x1; 513ms), 531ms total
INFO:     127.0.0.1:62452 - "GET /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba HTTP/1.1" 200 OK
INFO:     127.0.0.1:62452 - "OPTIONS /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/detection/case-match HTTP/1.1" 200 OK
2026-10-15 00:05:46,219 INFO perf: GET /documents/{id}/detection/case-match -> 7 supabase (GET documents x3, GET extracted_fields x3, GET cases x1; 1246ms), 1290ms total
INFO:     127.0.0.1:62452 - "GET /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/detection/case-match HTTP/1.1" 200 OK
2026-10-15 00:05:52,245 INFO perf: last 60s realtime/ws: ws.connect=1
2026-10-15 00:05:52,248 INFO perf: last 60s trace saves and other unscoped calls -> 1 supabase (GET documents x1; 398ms)
INFO:     127.0.0.1:61965 - "OPTIONS /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/extracted-fields HTTP/1.1" 200 OK
2026-10-15 00:05:59,168 INFO perf: PATCH /documents/{id}/extracted-fields -> 12 supabase (GET extracted_fields x5, GET documents x3, DELETE extracted_fields x1, POST extracted_fields x1, PATCH documents x1, PATCH cases x1; 3587ms), 3659ms total
INFO:     127.0.0.1:61965 - "PATCH /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/extracted-fields HTTP/1.1" 200 OK
2026-10-15 00:06:00,508 INFO perf: GET /documents/{id}/detection/case-match -> 7 supabase (GET documents x3, GET extracted_fields x3, GET cases x1; 1279ms), 1320ms total
INFO:     127.0.0.1:61965 - "GET /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/detection/case-match HTTP/1.1" 200 OK
INFO:     127.0.0.1:55577 - "OPTIONS /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/approve HTTP/1.1" 200 OK
2026-10-15 00:06:08,439 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: detection run starting
2026-10-15 00:06:08,993 INFO perf: POST /documents/{id}/approve -> 10 supabase (GET extracted_fields x5, GET documents x3, PATCH documents x1, PATCH cases x1; 2761ms), 2830ms total
INFO:     127.0.0.1:55577 - "POST /documents/62b0eed5-3716-4cf5-aff5-843d7da9d8ba/approve HTTP/1.1" 200 OK
INFO:     127.0.0.1:55577 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:49168 - "WebSocket /ws/cases/019a16e6-1f14-41d0-b56d-825e29eebf41?ticket=Naw5nJlURpX_D_6Cpq-bRB6NCktfxps4Es-5P5gQ7bg" [accepted]
INFO:     connection open
INFO:     127.0.0.1:55577 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:49669 - "POST /ws/tickets HTTP/1.1" 200 OK
INFO:     127.0.0.1:53473 - "WebSocket /ws/cases/019a16e6-1f14-41d0-b56d-825e29eebf41?ticket=uxFzwYAsuj4rR6IWFH2WIxyBelQpiiCfpN8n2BGRsfk" [accepted]
INFO:     connection open
2026-10-15 00:06:15,926 INFO perf: ws connect snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 967ms), 6897ms total
2026-10-15 00:06:15,927 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 708ms), 5940ms total
2026-10-15 00:06:15,928 INFO perf: ws connect snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 1733ms), 6349ms total
2026-10-15 00:06:18,711 INFO perf: GET /cases/{id}/analysis -> 5 supabase (GET cases x1, GET case_analysis_runs x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1; 1038ms), 1085ms total
INFO:     127.0.0.1:55393 - "GET /cases/019a16e6-1f14-41d0-b56d-825e29eebf41/analysis HTTP/1.1" 200 OK
2026-10-15 00:06:23,536 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: lead plan — briefs written for all specialists
2026-10-15 00:06:23,547 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: layout_rendering specialist starting
2026-10-15 00:06:23,582 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: content_validation specialist starting
2026-10-15 00:06:23,606 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: digital_authenticity specialist starting
2026-10-15 00:06:25,660 WARNING fraud_detection.ai_generation: ExifTool unavailable for AI provenance: [WinError 2] The system cannot find the file specified
2026-10-15 00:06:25,664 INFO fraud_detection.ai_generation: ai_generation document=62b0eed5-3716-4cf5-aff5-843d7da9d8ba image=file indicator='Provenance' assessed=False value=None strength=0.000 weight=0.00 contribution=0.0000 reason='ExifTool is not available to read provenance.'
Consider using the pymupdf_layout package for a greatly improved page layout analysis.
2026-10-15 00:06:26,631 ERROR app.ingestion.metadata_extractor: Metadata extraction failed for C008_CertIncumbency.pdf: exiftool is not installed on the server that processed this document
2026-10-15 00:06:30,065 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: digital_authenticity specialist done — 0 finding(s), tools called: ['metadata_tool', 'ai_generation_tool']
2026-10-15 00:06:33,899 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: layout_rendering specialist done — 0 finding(s), tools called: ['font_consistency_tool', 'layout_anomalies_tool', 'image_manipulation_tool', 'image_tampering_tool']
2026-10-15 00:06:52,274 INFO perf: last 60s realtime/ws: realtime.event=5, realtime.no_viewer=2, ws.connect=2, ws.push=1
2026-10-15 00:06:52,274 INFO perf: last 60s trace saves and other unscoped calls -> 4 supabase (PATCH langgraph_trace x4; 1091ms)
2026-10-15 00:06:53,548 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: content_validation specialist done — 8 finding(s), tools called: ['spelling_tool', 'date_consistency_tool', 'numerical_checks_tool', 'external_validation_tool']
2026-10-15 00:06:59,462 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: lead review done
2026-10-15 00:07:01,069 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: detection done — verdict flagged, 8 finding(s)
2026-10-15 00:07:03,669 INFO classifier_pipeline.langgraph.detection: Document 62b0eed5-3716-4cf5-aff5-843d7da9d8ba: 8 flag(s) stored
2026-10-15 00:07:04,136 INFO perf: detection doc=62b0eed5 -> 20 supabase (GET documents x5, GET storage x5, GET extracted_fields x4, PATCH documents x2, GET langgraph_trace x1, GET cases x1, DELETE flags x1, POST flags x1; 7749ms) | 8 claude (18151 in / 2175 out tokens), 55693ms total
2026-10-15 00:07:05,419 INFO perf: pushed snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 1240ms), 1282ms total
2026-10-15 00:07:06,345 INFO perf: realtime snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 1223ms), 1265ms total
2026-10-15 00:07:52,278 INFO perf: last 60s realtime/ws: realtime.event=1, ws.push=2
2026-10-15 00:07:52,278 INFO perf: last 60s trace saves and other unscoped calls -> 2 supabase (PATCH langgraph_trace x2; 399ms)
INFO:     127.0.0.1:49185 - "OPTIONS /cases/019a16e6-1f14-41d0-b56d-825e29eebf41/analysis/run HTTP/1.1" 200 OK
2026-10-15 00:07:54,814 INFO perf: POST /cases/{id}/analysis/run -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, POST case_analysis_runs x1; 1369ms), 1573ms total
INFO:     127.0.0.1:49185 - "POST /cases/019a16e6-1f14-41d0-b56d-825e29eebf41/analysis/run HTTP/1.1" 202 Accepted
2026-10-15 00:07:59,441 INFO perf: GET /cases/{id}/analysis -> 5 supabase (GET cases x1, GET case_analysis_runs x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1; 1230ms), 1269ms total
INFO:     127.0.0.1:49185 - "GET /cases/019a16e6-1f14-41d0-b56d-825e29eebf41/analysis HTTP/1.1" 200 OK
2026-10-15 00:08:42,195 INFO perf: pushed snapshot -> 5 supabase (GET cases x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1, GET case_analysis_runs x1; 1159ms), 1211ms total
2026-10-15 00:08:42,196 INFO perf: case analysis case=019a16e6 -> 3 supabase (GET case_analysis_runs x1, PATCH case_analysis_runs x1, POST risk_scores x1; 494ms) | 3 claude (9585 in / 2700 out tokens), 47537ms total
2026-10-15 00:08:43,304 INFO perf: GET /cases/{id}/analysis -> 5 supabase (GET cases x1, GET case_analysis_runs x1, GET documents x1, GET extracted_fields x1, GET langgraph_trace x1; 981ms), 1061ms total
INFO:     127.0.0.1:64027 - "GET /cases/019a16e6-1f14-41d0-b56d-825e29eebf41/analysis HTTP/1.1" 200 OK
2026-10-15 00:08:52,300 INFO perf: last 60s realtime/ws: ws.push=1
2026-10-15 00:08:52,301 INFO perf: last 60s trace saves and other unscoped calls -> 2 supabase (PATCH case_analysis_runs x2; 806ms)
