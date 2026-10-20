# 0007: A meeting timeline with topic and agency filters

Status: accepted product direction from the maintainer's feedback
Date: 2026-10-19
Source: maintainer design feedback

## Decision

The central organizing concept is a timeline of public meetings spanning past and future, with a visible marker for today. Upcoming meetings take priority in the initial view; access to the larger history must not crowd them out. Topics filter that timeline and reveal relevant agenda items within each meeting. Agencies are another filter, designed to accommodate more sources later.

Use one clear meeting destination instead of separate Meetings and Explore navigation. Keep corrections accessible from the footer and affected records. Four bookmarkable topic views remain available; their layout and interactions follow the shared timeline.

Use straightforward sans-serif typography, compact useful copy, and ordinary civic information design. Remove persuasive introductory text, self-referential trust explanations, routine "new" badges, and "sample" labels from the resident interface. Source status, cancellations, changed schedules, absent agendas, and missing logistics remain factual and visible where relevant. Removing prototype copy must not imply complete coverage or a live check that did not occur.

Increase verified recent and upcoming source coverage. Improve topic classification using item-specific evidence and agency context, with explicit regression cases for unrelated neighboring text and substantive transit items. Preserve human topic overrides.

## Implementation defaults

Start with a list timeline. Keep earlier meetings compact and expandable, show today clearly, and present upcoming meetings in date order. A calendar remains an option if it improves navigation after this layout is tested. Filters are bookmarkable and keyboard accessible, and work together rather than creating separate product sections.

This updates the topic-first presentation choice in decision 0002; its four topics, three launch agencies, and deferred alerts remain unchanged. It does not authorize cloud deployment, new agencies, subscriptions, or a chosen AI provider.
