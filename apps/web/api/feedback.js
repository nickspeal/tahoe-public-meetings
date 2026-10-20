// Vercel function: POST /api/feedback stores reader feedback and alerts the operator.
import { createFeedbackHandler } from '../server/feedback.mjs';

const handle = createFeedbackHandler();

export function POST(request) {
  return handle(request);
}
