# A Cloud-Native Event Booking Platform with Automatic Waitlisting 
ECE1779 Course Project

## 1. Motivation

### 1.1 Problem Context and Target Users

Events such as university workshops, classes, and conference sessions often attract more participants than their available capacity. When registration opens at a fixed time, many users may compete for the same limited seats simultaneously. The challenge continues after the initial allocation: bookings may be cancelled or remain unconfirmed beyond the allowed time, leaving seats that need to be reassigned fairly and promptly. This project addresses both concurrent booking and the subsequent release and reassignment of seats. Its intended users are event organizers, who need reliable capacity management and efficient waitlist handling, and attendees, who need fair access to available seats and clear information about their booking status and waitlist position.

### 1.2 Project Significance

These problems matter because allocation failures have direct consequences for both groups. Overbooking creates commitments that organizers cannot fulfill, increasing administrative work and undermining attendees’ trust. Delayed reassignment can leave seats unused even while interested participants remain on the waitlist. Unclear booking and waitlist information also forces attendees to check repeatedly and makes planning difficult. Addressing these issues can improve capacity utilization, reduce manual coordination, and make registration more predictable. The scenario also presents a meaningful cloud-native engineering challenge: the application must handle registration bursts through horizontal scaling while preserving capacity limits and first-come-first-served waitlist ordering. Booking records, confirmation deadlines, and waitlist state must persist across container restarts and redeployments so that infrastructure changes do not cause users to lose reservations or their place in line.

### 1.3 Limitations of Existing Approaches

Existing approaches illustrate why these requirements need to be considered together. Registration forms combined with manually maintained waitlists can collect requests, but organizers must still track cancellations, enforce confirmation deadlines, and contact waiting attendees. This coordination can delay reassignment and introduce mistakes. Similarly, a basic booking implementation that checks availability and records a reservation without appropriate concurrency control can allocate the same remaining seat to multiple users. These limitations motivate a cloud-native platform that combines concurrency-safe booking, time-limited reservations, automatic waitlist promotion, and real-time status updates. The project aims to demonstrate how these capabilities can work together to provide reliable and fair seat allocation under concurrent demand.

