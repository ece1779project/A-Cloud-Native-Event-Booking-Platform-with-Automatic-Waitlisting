# A Cloud-Native Event Booking Platform with Automatic Waitlisting 
ECE1779 Course Project - Team 19

## 1\. Motivation

### 1.1 Problem Context and Target Users

University workshops, classes, and conference sessions often attract more participants than they can accommodate. When registration opens, many users may compete for limited seats at once. Later, cancellations and unconfirmed bookings leave seats that must be reassigned fairly and promptly. This project addresses both stages: concurrent booking and the release and reassignment of seats. It serves event organizers, who need reliable capacity control and efficient waitlist management, and attendees, who need fair access to seats. Attendees also need clear booking status and waitlist positions so they know whether they have a confirmed place or are still waiting.

### 1.2 Project Significance

Overbooking leaves organizers unable to honor reservations, creates extra work, and damages attendees' trust. Slow reassignment can leave seats empty while people are still waiting. Unclear status information forces attendees to check repeatedly and makes planning difficult. Reliable booking and timely waitlist promotion can reduce these problems, improve seat utilization, and ease manual coordination. The project also examines how a cloud-native application can scale horizontally during registration peaks without exceeding capacity or disrupting first-come-first-served waitlist ordering. Booking records, confirmation deadlines, and waitlist state must survive container restarts and redeployments. Preserving this state ensures that infrastructure changes do not erase reservations or cause attendees to lose their place in line.

### 1.3 Limitations of Existing Approaches

Registration forms paired with manual waitlists require organizers to track cancellations, manage confirmation deadlines, and contact waiting attendees. These steps can delay reassignment and introduce errors. A basic booking system can also allocate the same seat twice if availability checks and reservation updates lack proper concurrency control. These limitations motivate combining reliable seat allocation with automatic waitlist handling, reducing the need for organizers to coordinate each cancellation and replacement themselves.

## 2\. Objectives, Architecture, and Key Features

### 2.1 Project Objectives and Core Functionality

This project aims to provide reliable event booking with strict capacity control and automatic waitlist promotion. It will prevent overbooking during concurrent requests, allocate released seats fairly, and provide real-time updates on availability and booking status. The platform will run on DigitalOcean with persistent storage, monitoring, and scalable deployment.

Organizers can create events, configure session capacity and registration deadlines, and view registrations. Attendees can browse events, reserve and confirm seats through simulated payment, cancel bookings, join waitlists, and accept time-limited offers. Administrators can manage users and events through role-based access controls.

### 2.2 Planned System Architecture

![Planned System Architecture](https://raw.githubusercontent.com/yinjw00/A-Cloud-Native-Event-Booking-Platform-with-Automatic-Waitlisting/main/planned-system-architecture.svg)

### 2.3 Required Technologies and Deployment

1. **Docker and Docker Compose:** We will containerize the FastAPI backend, WebSocket gateway, background workers, PostgreSQL, and Redis. Docker Compose will start the complete stack for consistent local development and integration testing. We will use uv and its lockfile to keep Python dependencies consistent across development and container builds.  
     
2. **PostgreSQL and Persistent Storage:** PostgreSQL will store users, events, bookings, waitlist entries, and reservation deadlines. Transactions and constraints will ensure that concurrent requests cannot overbook a session or create duplicate reservations, making it suitable for our booking system. Persistent volumes will preserve these records across restarts and redeployments.  
     
3. **Deployment Provider - DigitalOcean:** We will deploy on DigitalOcean Kubernetes, using DigitalOcean Volumes for persistent storage and internal cluster networking for PostgreSQL and Redis. DOKS will reduce cluster management work while allowing us to configure application deployment and storage. We will use doctl and kubectl for deployment tasks and Kubernetes Secrets for credentials.  
     
4. **Orchestration Approach - Kubernetes:** We will develop locally with minikube before deploying to DOKS. Deployments and Services will manage application replicas and networking, while StatefulSets and PersistentVolumeClaims will manage PostgreSQL and Redis storage. Health probes will support failure recovery, and Horizontal Pod Autoscalers will scale stateless components to handle booking spikes.  
     
5. **Monitoring and Observability:** We will use DigitalOcean Monitoring for infrastructure metrics and Prometheus with Grafana for application monitoring. The Python prometheus-client library will expose booking throughput, request latency, errors, and capacity violations. Dashboards and alerts will track resource usage, Redis memory, and worker backlog to help identify bottlenecks and verify system behavior under load.

### 2.4 Planned Advanced Features

6. **Real-Time Updates:** After booking or waitlist changes are committed to PostgreSQL, Redis Pub/Sub and WebSockets will deliver updates to the frontend. Users will see seat availability, booking results, and waitlist offers without refreshing the page.  
     
7. **Security Enhancements:** Authentication and permission checks will protect API endpoints so organizers can manage only their own events and attendees can access only their own bookings. Redis-backed rate limiting will protect booking endpoints from excessive requests, while HTTPS will secure communication.  
     
8. **Auto-Scaling and Application Availability:** Kubernetes HPA will adjust API and WebSocket gateway replicas based on resource usage, while load balancing and health checks will distribute traffic and support recovery from instance failures. This suits our project because registration openings can attract sudden bursts of requests, followed by quieter periods. We will use load tests to evaluate scaling and limit database connections to avoid overwhelming PostgreSQL as replicas increase.

### 2.5 Scope, Feasibility, and Risks

We will focus on event booking, temporary seat holds, and automatic waitlist promotion, with a lightweight frontend. Seat maps, multiple ticket types, and real payment processing are outside the project scope. The core workflow is straightforward, allowing us to prioritize a working booking flow before adding real-time updates and scaling within the project timeline.

The main risks are overbooking under concurrent requests, conflicts between confirmation and expiration, and cloud integration issues. We will address these through PostgreSQL transactions and constraints, concurrency testing, and early deployment checks. A single PostgreSQL instance will simplify deployment but remain a potential point of failure.


## 3\. Project Plan and Team Responsibilities

### 3.1 Development Milestones

Development will proceed through five verifiable milestones, with overlapping activities and approximate durations. The presentation checkpoint requires a cloud-deployed system with real-time updates.

**Milestone 1 — Contracts and deployment foundation (Est. 3 days).** Define reservation states, REST APIs, and event formats; set up Docker Compose, Kubernetes manifests, and a DigitalOcean Kubernetes (DOKS) cluster with persistent PostgreSQL storage. **Completion:** run the local stack, deploy a minimal application, and verify database persistence after a pod restart.

**Milestone 2 - Feature development and integration (Est. 2 weeks).** Develop booking, capacity controls, waitlist and expiration workers, and the WebSocket gateway in parallel; integrate the reservation lifecycle on DOKS. **Completion:** components pass interface tests, and the cloud deployment passes concurrency, retry, and persistence tests without overselling.

**Milestone 3 — Presentation-ready system (Est. 2 days; presentation checkpoint).** Integrate Redis Pub/Sub, WebSocket updates, and attendee/organizer interfaces; configure HTTPS, role-based access, and basic monitoring. **Completion:** demonstrate an oversubscribed event, automatic waitlist offers, and live capacity and queue-position updates.

**Milestone 4 — Scaling and evaluation (Est. 1 week).** Configure Kubernetes Horizontal Pod Autoscalers (HPA) and test throughput, latency, correctness, and selected failures under load. **Completion:** document reproducible experiments, scaling behavior, and limitations. Backup/recovery and serverless notifications are optional extensions.

**Milestone 5 — Final delivery (Est. 3 days).** Finalize deployment instructions, experimental results, design trade-offs, the report, and demonstration video. **Completion:** submit the code, report, and video with a working DOKS deployment.

### 3.2 Team Responsibilities

Primary ownership is assigned by system capability.

**Jiawei - Booking and API.** Lead authentication, event and session management, PostgreSQL models, and capacity enforcement, including concurrency tests to prevent overselling.

**Jianye — Waitlist and Workers.** Lead waitlist management, expiration handling, and automatic seat offers, ensuring reliable processing, ordered promotion, idempotency, and Redis/PostgreSQL consistency.

**Yifeng — Real-Time and Cloud.** Lead the WebSocket gateway, lightweight interface, DOKS deployment, persistent storage, monitoring, autoscaling, and infrastructure performance evaluation.

All members will review interfaces, test integration, verify deployment, and prepare the presentation. Jiawei and Jianye will jointly validate reservation correctness; Yifeng will support cloud testing.

### 3.3 Integration Strategy

**Contracts and shared state.** Agree on API schemas, reservation transitions, and event payloads before implementation. PostgreSQL will hold authoritative reservation data; Redis will support coordination and messaging. Mock endpoints and event fixtures will enable independent development, with team review for contract changes.

**Booking and waitlist lifecycle.** The API and workers will share a state model. A transactional outbox and Redis Streams will connect committed database changes to asynchronous processing, with idempotent consumers handling retries. End-to-end tests will verify capacity release after expiration or cancellation and ordered waitlist promotion.

**Real-time interface and deployment.** Redis Pub/Sub will deliver committed changes to the WebSocket gateway. Clients will use REST for commands and initial state, and WebSockets for updates. Docker Compose and Kubernetes manifests will support local and DOKS integration checks at each milestone.

### 3.4 Dependencies, Coordination, and Risks

Booking, expiration, and waitlist promotion depend on a common state model. Joint interface reviews will keep the end-to-end workflow deployable.

Key risks include concurrency errors, Redis/PostgreSQL inconsistencies, and stateful Kubernetes operations. Mitigations include database constraints, idempotent processing, reconciliation checks, persistent volumes, and restart/concurrency tests. Deployment will begin in Milestone 1 to expose infrastructure issues early. If time or resources become limited, we will prioritize correct booking and waitlist behavior, persistent DOKS deployment, and real-time updates for the presentation, reducing optional integrations or later benchmark scope.

## 4\. Initial Team Reasoning and AI-Assisted Exploration

### 4.1 Initial Team Reasoning

We began with four candidate ideas: a real-time auction platform, a flash sale system, an event booking platform with automatic waitlisting, and a job recruitment platform. Our main question in choosing among them was which idea would make state, concurrency, and scaling essential to the problem itself, rather than features added on for the sake of the course. The recruitment platform was ruled out first, because it is mostly CRUD and its hard problems, such as search and candidate matching, have little to do with cloud infrastructure. The auction and flash sale ideas both involved genuine concurrency, but their workflows were short, centred on one hot item and a single purchase.

Our working hypothesis was that event booking with a waitlist contains the concurrency problem of a flash sale while adding long-lived state in the form of holds, expirations, cancellations, and promotions. We also assumed from the start that correctness should be enforced in PostgreSQL, with Redis acting as an accelerator, and that Kubernetes would suit us better than Docker Swarm because we expect to rely on StatefulSets, PersistentVolumeClaims, and the Horizontal Pod Autoscaler.

Several questions remained open at this stage. We did not know whether pre-deducting seats in Redis would be measurably faster than database locking at our scale, or whether any gain would justify the extra consistency risk. 

### 4.2 Exploration and Evaluation

We used an AI assistant to compare the four ideas, to brainstorm possible architectures, and to explain techniques that were new to us. It proposed merging flash-sale techniques into the booking platform, together with a three-layer defence against overselling, a transactional outbox, and a delay queue built on Redis sorted sets.

For some decisions we compared alternatives ourselves. For messaging, we weighed Redis Streams against Kafka and RocketMQ. The latter two offer richer features, and RocketMQ in particular provides delayed and transactional messages out of the box, but each would require running a separate cluster, whereas Redis is already part of our stack. For real-time updates, we compared polling, Server-Sent Events, and WebSocket. Because our updates flow only from server to client, Server-Sent Events would be sufficient. We currently plan to use WebSocket, but the Redis Pub/Sub design behind it would not change if we switched, and real-time push is the first feature we would cut if the scope had to shrink.

Finally, some questions cannot be settled by discussion with an AI assistant. Whether Redis pre-deduction justifies its added complexity will be decided by our own load-test comparison against the database-only approach.