bq query --use_legacy_sql=false "SELECT DATE(time) AS day, sku, ROUND(SUM(cost),2) AS cost FROM fyp-six-sevens.billing_export.costs WHERE service = 'Artifact Registry' AND time >= '2026-11-10' GROUP BY day, sku ORDER BY day, cost DESC"
+------------+---------------------------------------------------------------------------------------------+------+
|    day     |                                             sku                                             | cost |
+------------+---------------------------------------------------------------------------------------------+------+
| 2026-11-10 | Artifact Registry Storage                                                                   | 0.01 |
| 2026-11-11 | Artifact Registry Network Internet Egress AsiaPacfic to AsiaPacfic                          | 0.18 |
| 2026-11-11 | Artifact Registry Storage                                                                   | 0.01 |
| 2026-11-12 | Artifact Registry Storage                                                                   | 0.01 |
| 2026-11-13 | Artifact Registry Network Internet Egress AsiaPacfic to AsiaPacfic                          | 0.07 |
| 2026-11-13 | Artifact Registry Storage                                                                   | 0.01 |
| 2026-11-14 | Artifact Registry Network Internet Egress AsiaPacfic to AsiaPacfic                          | 3.02 |
| 2026-11-14 | Artifact Registry Network Internet Egress Intercontinental (Excl Oceania, Africa and China) | 0.38 |
| 2026-11-14 | Artifact Registry Storage                                                                   | 0.01 |
| 2026-11-14 | Artifact Registry GOOGLE-API Egress                                                         |  0.0 |
| 2026-11-15 | Artifact Registry Network Internet Egress AsiaPacfic to AsiaPacfic                          | 0.79 |
| 2026-11-15 | Artifact Registry Storage                                                                   | 0.01 |
| 2026-11-15 | Artifact Registry GOOGLE-API Egress                                                         |  0.0 |
| 2026-11-16 | Artifact Registry Network Internet Egress AsiaPacfic to AsiaPacfic                          | 1.65 |
| 2026-11-16 | Artifact Registry Storage                                                                   | 0.01 |
| 2026-11-16 | Artifact Registry GOOGLE-API Egress                                                         |  0.0 |
| 2026-11-17 | Artifact Registry Network Internet Egress AsiaPacfic to AsiaPacfic                          | 2.12 |
| 2026-11-17 | Artifact Registry Storage                                                                   | 0.01 |
+------------+---------------------------------------------------------------------------------------------+------+

(.venv) C:\Users\LENOVO\Documents\GitHub\six-sevens\deploy\terraform>gcloud artifacts docker images list asia-southeast1-docker.pkg.dev/fyp-six-sevens/app --include-tags --format="table(package.basename(),tags,createTime.date(),sizeBytes)" --sort-by=~sizeBytes --limit=40
Listing items under project fyp-six-sevens, location asia-southeast1, repository app.

IMAGE: api
TAGS: 151ac87b5d1bd58ea61d5d1f55099eebc88e681e
CREATE_TIME: 2026-11-14T16:11:20
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-548d89b75d040bd7deb2f6256998c94448a2e6af1d1ebd1437a035189f916f2f.sig
CREATE_TIME: 2026-11-14T13:50:12
SIZE_BYTES: 

IMAGE: api
TAGS: 6035ea2502387390490c4a90da112acb63103319
CREATE_TIME: 2026-11-14T12:28:19
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-35e9db29f5cf153bf1b504c4dc059e0515b6945886ff30b44944ad1cf38222f7.sig
CREATE_TIME: 2026-11-16T14:06:38
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-492997dc8bf8cc87cede95f0a131024c01ed588eaa82cfb02416647f2c6368a8.att
CREATE_TIME: 2026-11-14T14:56:18
SIZE_BYTES: 

IMAGE: api
TAGS: 4a7bbfc61e2dc9d59a7e18162f8a3797ea629126
CREATE_TIME: 2026-11-16T15:12:49
SIZE_BYTES: 

IMAGE: api
TAGS: cb604316a85f9b549c82cc7345fd2a3f47bbe78a
CREATE_TIME: 2026-11-17T14:41:55
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-cdb7754b7e8777b0d5d8be18757e2bb1098aa6f4959935fe6a4129281d352e8a.sig
CREATE_TIME: 2026-11-14T15:40:35
SIZE_BYTES: 

IMAGE: api
TAGS: cfd12a467f9da495cd96129fb026082b2e56beb8
CREATE_TIME: 2026-11-14T17:47:59
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-2d8f82f13b9a656eed6f10fd8bda0a379dbd242f9ff324b5af89d0cd2236770e.sig
CREATE_TIME: 2026-11-17T05:52:13
SIZE_BYTES: 

IMAGE: api
TAGS: 4a11ea181e2a912b5a34bfade6b53e69d57774d6
CREATE_TIME: 2026-11-14T14:11:07
SIZE_BYTES: 

IMAGE: api
TAGS: a106bb934990459ce07b47101adcc6c26cb7621c
CREATE_TIME: 2026-11-17T05:45:11
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-15848d58b738e0b97b86af9b98be16ea388fdaefec87ea70e56fde83e0aff290.sig
CREATE_TIME: 2026-11-16T15:17:34
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-0c1cd4b1dbe405892c859fc483b0857551016c7bf1617e11f2fbac89879f7299.att
CREATE_TIME: 2026-11-14T16:17:00
SIZE_BYTES: 

IMAGE: api
TAGS: a8a560f9937ba86fb9ee2c004b1f88a8d743e405
CREATE_TIME: 2026-11-15T15:53:55
SIZE_BYTES: 

IMAGE: api
TAGS: d6cb79cad03799ac039cc327fd30b0f9e003fe81
CREATE_TIME: 2026-11-16T14:00:56
SIZE_BYTES: 

IMAGE: api
TAGS: 82741efbbdec0a5edbcdc23cc92fabe81d35c28f
CREATE_TIME: 2026-11-14T14:20:18
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-8077dfc3a75fe5b9502461e3d2dfc04bd0caf22483b2da66a8caaedcd791d8fd.sig
CREATE_TIME: 2026-11-16T16:43:29
SIZE_BYTES: 

IMAGE: api
TAGS: 73f125e07a3845767ce8b3e60fcf43aa547012fa
CREATE_TIME: 2026-11-15T16:31:15
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-f250609fc348b9215c6faa05ad6e7999b1ff38a541c44ca539031ea1930972b1.att
CREATE_TIME: 2026-11-17T11:28:01
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-a3f6dc334f6ae3a8ae5e4e269399437508492798e34bda7ce094f68bf840fcb6.att
CREATE_TIME: 2026-11-17T04:33:39
SIZE_BYTES: 

IMAGE: api
TAGS: a38e4766795afcce29850d56fb36532db39caac0
CREATE_TIME: 2026-11-14T14:51:54
SIZE_BYTES: 

IMAGE: api
TAGS: 8ee467e58093b8df65c18bd0a43e1956d6f4e503
CREATE_TIME: 2026-11-13T12:18:28
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-259282cf6032daadd151d0594df5f73f148956c185a5accd65000b324822f587.sig
CREATE_TIME: 2026-11-14T14:15:18
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-492997dc8bf8cc87cede95f0a131024c01ed588eaa82cfb02416647f2c6368a8.sig
CREATE_TIME: 2026-11-14T14:56:08
SIZE_BYTES: 

IMAGE: api
TAGS: a0503e21a84d81dda376beaa466aecb84aa6ed11
CREATE_TIME: 2026-11-14T13:46:48
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-183378af91e28cacd6957056a2dbcd9698568813fb084dbf1d48190e54c41c82.sig
CREATE_TIME: 2026-11-17T14:47:56
SIZE_BYTES: 

IMAGE: api
TAGS: 
CREATE_TIME: 2026-11-17T14:48:07
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-a3f6dc334f6ae3a8ae5e4e269399437508492798e34bda7ce094f68bf840fcb6.sig
CREATE_TIME: 2026-11-17T04:33:28
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-15848d58b738e0b97b86af9b98be16ea388fdaefec87ea70e56fde83e0aff290.att
CREATE_TIME: 2026-11-16T15:17:45
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-f9a1acc74f67252c2643d34be9c7267e44032e14ed4e30e61d7a21e0644ed448.att
CREATE_TIME: 2026-11-16T15:27:08
SIZE_BYTES: 

IMAGE: api
TAGS: 
CREATE_TIME: 2026-11-17T11:27:55
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-cf6f7b0e267cef851611dd65b09ed87faca6a233f12866a3cfcd6c0d4b7ba0b3.sig
CREATE_TIME: 2026-11-14T18:15:05
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-548d89b75d040bd7deb2f6256998c94448a2e6af1d1ebd1437a035189f916f2f.att
CREATE_TIME: 2026-11-14T13:50:24
SIZE_BYTES: 

IMAGE: api
TAGS: 99d7c5da4247ab020fa4e47687ce9ebc7b3a7ed2
CREATE_TIME: 2026-11-11T05:01:38
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-259282cf6032daadd151d0594df5f73f148956c185a5accd65000b324822f587.att
CREATE_TIME: 2026-11-14T14:15:27
SIZE_BYTES: 

IMAGE: api
TAGS: 
CREATE_TIME: 2026-11-17T05:52:24
SIZE_BYTES: 

IMAGE: api
TAGS: sha256-32863a04c0ec7ffc073494514d57c33bc17a770b2ec5fcdac70d1b30112503bd.sig
CREATE_TIME: 2026-11-15T15:58:58
SIZE_BYTES: 

IMAGE: api
TAGS: 399f2f9baf423b00a06d88a907ee46c021a7df4b
CREATE_TIME: 2026-11-16T16:35:38
SIZE_BYTES: 

IMAGE: api
TAGS: a85b6d39dd0465f54a5bf304074e7d71019d2657
CREATE_TIME: 2026-11-14T11:50:56
SIZE_BYTES: 