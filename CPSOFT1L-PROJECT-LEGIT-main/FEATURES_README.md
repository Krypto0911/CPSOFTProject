# Updated NU Study project

## Features added
- **Sequential prerequisite gating:** students must complete the preceding modules in course creation order. A preceding quiz/exam must also meet its configured passing percentage (default 70%) to unlock the next module. Access is checked server-side.
- **Quiz/exam time limits:** instructor sets minutes (0 means untimed). The first student page visit records the start in the database; countdown display and server-side deadline validation are enforced. Assessments can be submitted only once. Question order and each question’s answer display order are shuffled consistently for each student.
- **Grade posting and course locking:** course instructor can post grades, then permanently lock records. Locking blocks grading and new submissions; posted assignment grades become visible to students. There is no unlock button.
- **Exports:** instructor can download syllabus, roster, and master grades CSV files. These can be opened in Excel or printed. Export routes check instructor authorization.

## Setup
```powershell
python -m pip install -r requirements.txt
python app.py
```
Use Python 3.11 or 3.12 for best compatibility with the provided pinned dependencies. If you have a prior database, start the app normally to run additive schema migrations. Back up the database before upgrading. Use environment variables (especially SECRET_KEY and DATABASE_URL) for deployment. An existing SQLite database stays in the project folder and is not bundled here.

## Notes / current limits
- The exports are CSV, not PDF. Print them using Excel / spreadsheet software.
- Record locking is irreversible in the UI; an administrator would need a manual database change to unlock.
- The displayed countdown is for convenience; submission validation is enforced at the server using UTC timestamps.
- Quiz attempts are single-submission; students who fail need instructor intervention (there is no retake feature).
- Question randomization is stable per student, but options are reshuffled in the browser; answer values remain A/B/C/D to preserve correct grading.
- This package was statically checked; run end-to-end integration tests with your configured database before production rollout.

## Timed assessment creation (instructor UI)
Open a course as its instructor and select **Create Timed Quiz / Exam** beside **Add Module**. Provide a title, section, 1-1440 minute timer, passing score and first multiple-choice question. The new quiz/exam is saved with its first question and opens its module management page to add more questions. The course list displays assessment duration. Timers are enforced on the server for student attempts.

## Week 1–13 and mixed assessment questions
- Dashboard shows Week 1–13 links for each enrolled/owned course, and course detail shows thirteen weekly sections even when empty.
- Instructors can create a timed quiz or exam starting with A–D multiple choice or True/False. Each additional question can use either format, so one exam can mix types.
- Student forms render both question types, and existing automatic grading supports their stored correct-answer codes.
- Existing database schemas receive a backward-compatible `quiz_questions.question_type` migration on application startup.
- Automated browser/database tests and production security audit are still required before deployment.

## October 21 update: open weeks, Identification, registration feedback
- All enrolled students may view and complete Week 1 through Week 13 in any order, without earlier-module prerequisite errors. Enrollment authorization still applies.
- Both assessment creation and Add Question now offer Identification (short answer) in addition to multiple choice and true/false, including mixed assessments. An Identification answer is automatically graded by exact text after trimming/normalizing spaces, ignoring case; synonyms or alternate wordings are not matched automatically.
- Registration password errors are displayed as friendly on-form messages instead of raw validation exception strings. Password format guidance appears under the field.
- Existing features and timed assessment deadline enforcement are retained.

## Calendar and notifications update
- Quiz, exam, assignment, and lecture due dates are shown automatically in enrolled students' calendars and the instructor's calendar.
- Changing a due date updates the calendar event; deleting a module removes it.
- Newly created assessments/modules notify all currently enrolled students, with the configured due date. Edits also notify them.
- No duplicate calendar event is inserted during new module creation. Course deadlines are generated from the source records.
- Teacher-created deadlines are read-only on the calendar; edit them through the course module.
- Notifications are in-app (not push or email); students who enroll after publication can still see upcoming course dates on their calendar, but do not receive a historical notification.

## Gradebook and printable roster (October 2026 update)

- Within a course, select **Gradebook** in the course sidebar. Students can view only their own quiz/exam scores, manually graded assignments, feedback, category averages, and the running overall percentage. They can print their grade report from the browser.
- Instructors see an additional **Class Roster** link, can switch between enrolled students, print the roster and scores, and override a quiz, exam or assignment percentage (0–100) with optional feedback. The instructor grade supersedes the auto-calculated score. Edits are refused once records are locked.
- **Grade calculation:** each graded activity counts equally, based on its percentage. Overall = sum(graded activity percentages) / number of graded activities. The category cards show individual quiz/assignment/exam averages. Ungraded activities are not counted; this is a *running* grade, not a final posted institutional grade. No unspecified category weights are assumed.
- On every successful sign-in, the dashboard displays a personalized welcome and the student/instructor receives an in-app welcome notification.
- To also send an actual welcome email, set `SMTP_HOST`, `SMTP_FROM`, `SMTP_PORT` (default `587`), `SMTP_USER`, and `SMTP_PASSWORD` in your `.env`. SMTP credentials are **not** included in this ZIP. If SMTP is not configured or the email fails, login succeeds but no actual external email is delivered.
- Existing database tables are reused; your existing records do not need to be erased. **Keep your current database file and `.env` when updating the project.**
- Verification performed: Python syntax and Jinja template syntax checks. A full end-to-end database/browser test is required with your installation and credentials.

## Quiz/exam timeout auto-submission
- Students' typed/selected answers are saved to the server when changed and every five seconds while an assessment is open.
- The countdown automatically submits the form when it reaches zero, without requiring all questions to be answered.
- The server locks assessment submissions at their original deadline. If a request arrives after the deadline, only the most recently saved answers from before the deadline are graded; blank answers receive zero credit.
- Opening an expired assessment finalizes its saved draft even if the original browser tab was closed. A weak/disconnected network can prevent the last unsaved edits from reaching the server, so students should stay online.


## Assignment attachment and navigation fix
- Teacher content-block type selector now switches between text, file chooser, and external URL fields.
- Uploaded file is saved with collision-resistant name and has a download button; URLs show an external link.
- Student assignment upload uses unique safe names, checks file extension and size, and rejects blank files.
- Gradebook/roster page now follows the same visual shell and navigation as Course Modules.
- The application still requires a running database and end-to-end browser verification.
