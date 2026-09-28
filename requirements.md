Lab 03: Migration Workflow Agent
Objective
Build a multi-step agent that can migrate code between frameworks using the planning pattern.

Time Allotted: 1 hour 45 minutes

Learning Goals
Implement the planning agent pattern
Build multi-step workflows with verification
Handle complex tasks with tool-use
Manage state across agent iterations
What You'll Build
An agent that can:

Analyze source code to understand its structure
Create a migration plan
Execute migration steps
Verify the migration worked
┌─────────────────────────────────────────────────────────────┐
│ Migration Agent Workflow │
├─────────────────────────────────────────────────────────────┤
│ │
│ Input: Source files + target framework │
│ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ PHASE 1: ANALYSIS │ │
│ │ • Parse source files │ │
│ │ • Identify patterns and dependencies │ │
│ │ • Detect potential issues │ │
│ └─────────────────────────────────────────────────────┘ │
│ │ │
│ ▼ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ PHASE 2: PLANNING │ │
│ │ • Create step-by-step migration plan │ │
│ │ • Identify dependencies between steps │ │
│ │ • Estimate complexity per step │ │
│ └─────────────────────────────────────────────────────┘ │
│ │ │
│ ▼ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ PHASE 3: EXECUTION │ │
│ │ • Execute each step in order │ │
│ │ • Generate migrated code │ │
│ │ • Track progress and results │ │
│ └─────────────────────────────────────────────────────┘ │
│ │ │
│ ▼ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ PHASE 4: VERIFICATION │ │
│ │ • Check migrated code compiles/runs │ │
│ │ • Identify any remaining issues │ │
│ │ • Generate migration report │ │
│ └─────────────────────────────────────────────────────┘ │
│ │
│ Output: Migrated files + report │
│ │
└─────────────────────────────────────────────────────────────┘
Requirements
Core Functionality
POST /migrate endpoint accepting source files, source framework, and target framework
Agent state management with phases: analysis, planning, execution, verification
Migration plan generation with:
Step descriptions
Dependencies between steps
Status tracking (pending, in_progress, completed, failed)
Code generation for each migration step
Verification phase to validate migrated code
JSON response with: success status, migrated files, executed plan, verification result, errors
Frontend Requirements
Web interface to input source code files and select source/target frameworks
Real-time progress display showing current phase (Analysis → Planning → Execution → Verification)
Migration plan viewer with step status
Output panel showing migrated code with diff view
Responsive design
Language Choice
Aspect Python TypeScript
Framework FastAPI Hono
State dataclasses interfaces + functions
Run uvicorn main:app --reload npm run dev
Deliverables
Working migration agent with all 4 phases
Proper state management
Plan creation and execution
Verification step
Deployed to Railway/Vercel
Web frontend with migration workflow visualization
Application deployed to Vercel/Railway/Render (provide URL)
Extension Challenges
Rollback Support: Add ability to rollback failed migrations
Parallel Execution: Execute independent steps in parallel
Human Approval: Add human-in-the-loop for plan approval
Multiple Frameworks: Support more source/target combinations
