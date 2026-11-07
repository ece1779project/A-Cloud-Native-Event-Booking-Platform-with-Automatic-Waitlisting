# A Cloud-Native Event Booking Platform with Automatic Waitlisting

ECE1779 Course Project

## Overview

Many events have far more demand than capacity, such as workshops, classes, and conference sessions. When registration opens, a large number of users try to book a small number of seats at the same time. Some bookings are later cancelled or never confirmed, and those seats should go to the people still waiting.

This project is a cloud-native event booking platform built for that scenario:

- **Organizers** create events with limited capacity and a registration open time.
- **Attendees** book seats concurrently. A booking is held for a limited time and must be confirmed.
- **When an event is full**, attendees join a waitlist. If a booking is cancelled or expires, the seat is automatically offered to the next person in line.
- **Real-time updates:** remaining seats and waitlist positions are pushed to users.

## Goals

- **No overselling:** a seat is never sold twice, even under heavy concurrent load.
- **Fair waitlisting:** freed seats are offered in first-come-first-served order.
- **Scalability:** the application scales horizontally on Kubernetes.
- **Durable state:** data persists across container restarts and redeployments.

