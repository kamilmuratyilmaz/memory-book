# Role

You are a senior full-stack engineer, AI product engineer, and UI/UX engineer.

Your task is to build a production-quality web application for creating beautiful personal memory books with AI.

You are an autonomous coding agent working directly inside the provided development container and repository.

Do not merely explain what should be built.

Inspect the repository, implement the product, run it, test it, and fix problems you encounter.

Reuse the existing project architecture and tooling whenever practical.

---

# Product

Build an **AI-powered Memory Book web application**.

The core idea is simple:

> The user gives the AI photos, memories, notes, dates, locations, and preferences. The AI creates a complete memory book.

The application then allows the user to:

- review the generated book
- edit it
- read it as an interactive digital book
- export it as a print-ready PDF

The AI is the **primary creator**.

The editor is the **refinement layer**.

The reader is the **presentation layer**.

The PDF renderer is the **print/export layer**.

---

# Core Product Experience

The ideal user flow is:

```text
User provides memories
        ↓
AI understands and organizes them
        ↓
AI creates the story
        ↓
AI creates the page structure
        ↓
AI chooses layouts and places photos
        ↓
AI generates the memory book
        ↓
User reviews / edits
        ↓
Interactive digital book
        +
Print-ready PDF
```

The user should not feel like they are using a complicated design tool.

The experience should feel like:

> "Give us your memories, and we'll turn them into a beautiful book."

---

# Environment

This is a **web application**.

Work directly inside the provided development container.

You must:

- inspect the repository
- understand the existing stack
- modify the repository directly
- install dependencies when necessary
- run the application locally
- run tests
- run linting/type checks when available
- validate the UI
- validate PDF generation
- fix implementation issues

Do not stop after creating a conceptual prototype if the repository supports full implementation.

Do not provide code snippets instead of implementing the feature.

---

# Product Principles

Prioritize:

1. Simplicity
2. Emotional storytelling
3. Visual quality
4. Automation
5. Ease of editing
6. Print fidelity
7. Performance

The application should feel closer to a premium personal publishing product than an enterprise SaaS dashboard.

Avoid:

- generic admin dashboard aesthetics
- excessive configuration
- spreadsheet-like interfaces
- technical terminology exposed to the user
- overwhelming controls

Prefer:

- editorial layouts
- elegant typography
- strong photography
- whitespace
- subtle paper textures
- restrained animation
- tasteful decoration
- coherent visual hierarchy

---

# AI Is the Main Creator

The central feature is AI-generated book creation.

The user may provide:

- photos
- text notes
- diary entries
- dates
- locations
- names
- trip information
- optional descriptions
- optional instructions about mood or style

The AI should transform this information into a structured `MemoryBook`.

The AI may:

- organize memories chronologically
- group related photos
- identify events
- infer chapters
- create chapter titles
- write concise captions
- transform rough notes into polished prose
- choose important photos
- determine image hierarchy
- choose page templates
- determine how many pages are needed
- create a cover
- select typography and visual style
- place content on pages

The generated output must remain editable.

Do not flatten the AI output into a single image or opaque HTML document.

---

# AI Generation Pipeline

Conceptually implement:

```text
Raw User Input
      ↓
Ingestion
      ↓
Understanding
      ↓
Memory Extraction
      ↓
Story / Chapter Planning
      ↓
Page Planning
      ↓
Layout Selection
      ↓
MemoryBook JSON
      ↓
Rendering
```

Keep the intermediate structured representation explicit.

The AI should generate structured data, not arbitrary UI code.

For example:

```ts id="g3xq7b"
type GeneratedMemoryBook = {
  title: string;
  subtitle?: string;
  chapters: Chapter[];
  pages: MemoryPage[];
  theme: ThemeConfig;
};
```

The exact schema may differ depending on the repository, but the architecture must preserve the separation between:

- user input
- AI reasoning/output
- document model
- rendering

---

# Document Model

The most important architectural requirement is a **single source of truth** for the book.

Use one structured `MemoryBook` document model.

Conceptually:

```ts id="qk9h6u"
type MemoryBook = {
  id: string;

  title: string;
  subtitle?: string;
  author?: string;

  cover?: ImageAsset;

  pageSize: PageSize;
  orientation: "portrait" | "landscape";

  theme: ThemeConfig;

  pages: MemoryPage[];

  metadata?: BookMetadata;
};

type MemoryPage = {
  id: string;

  chapterId?: string;

  elements: PageElement[];

  background?: BackgroundConfig;
};

type PageElement =
  | TextElement
  | HeadingElement
  | ImageElement
  | CollageElement
  | QuoteElement
  | MetadataElement
  | DividerElement
  | SpacerElement
  | DecorativeElement;
```

This model is the source of truth for:

- editor
- reader
- PDF renderer
- future image export
- future physical printing integration
- sharing / public pages
- future AI revisions

Do not create separate incompatible models for these outputs.

---

# Critical Architecture Invariant

The following invariant must be preserved:

```text
                 MemoryBook
                     ↓
        ┌────────────┼────────────┐
        ↓            ↓            ↓
      Editor       Reader        PDF
        ↓            ↓            ↓
      Editing    Digital Book   Printing
```

One document.

Multiple renderers.

Do not duplicate the book content separately for web and PDF.

---

# Book Creation

A user should be able to create a book by supplying some combination of:

- title
- photos
- memories
- dates
- locations
- author
- desired mood
- desired theme
- approximate book length

The user should not have to fill in every field.

Use sensible defaults.

Example:

```text
Create your memory book

[ Upload Photos ]

[ Tell us about this memory ]

Optional:
• Date
• Location
• People
• Style

         [ Create my book ]
```

The AI should then generate a first complete draft.

---

# AI Generation UX

Generation should feel understandable and progressive.

Do not display meaningless technical progress such as:

> "Running inference..."

Instead use user-facing stages such as:

```text
Understanding your memories
        ↓
Organizing your photos
        ↓
Building your story
        ↓
Designing your pages
        ↓
Finishing your book
```

The user should be able to understand what the AI is doing without seeing internal implementation details.

---

# AI Revision

The user should be able to ask the AI to modify the existing book.

Examples:

- "Make this more emotional."
- "Use fewer words."
- "Add more photos."
- "Make this page minimalist."
- "Turn this into a 10-page book."
- "Make the cover more elegant."
- "Move this photo to the previous page."
- "Write a better caption."
- "Create a separate chapter for Shanghai."
- "Make the whole book feel more vintage."

AI changes should modify the structured document rather than regenerating the entire book unnecessarily.

Prefer targeted transformations.

For destructive or large-scale changes, preserve the ability to undo.

---

# Page Templates

The AI should be able to choose from reusable page templates.

Provide templates such as:

- Full-page photograph
- Photo + text
- Two-photo layout
- Three-photo collage
- Four-photo grid
- Quote page
- Timeline page
- Travel page
- Chapter opener
- Minimal text page
- Closing page
- Map/location page

Templates must generate structured page elements.

Do not implement templates as screenshots.

Templates should be data-driven and reusable.

---

# Themes

Implement a theme system.

Each theme may define:

- typography
- color palette
- page background
- heading style
- body text style
- image treatment
- image frame
- spacing
- decorative elements
- cover style

Provide several initial themes, such as:

- Minimal
- Editorial
- Travel Journal
- Scrapbook
- Vintage
- Romantic

The system should make future themes easy to add.

---

# Page Editor

The AI creates the initial book.

The user should then be able to visually refine it.

The editor should support:

- selecting pages
- adding pages
- deleting pages
- duplicating pages
- reordering pages
- editing text
- replacing images
- moving elements
- resizing elements
- cropping images
- changing image position
- changing typography
- changing alignment
- changing colors
- changing backgrounds
- applying templates
- changing theme
- undo
- redo

The user should not need to know CSS.

---

# Editor Layout

A practical desktop editor can use:

```text
┌─────────────────────────────────────────────────────────────┐
│                    Memory Book Editor                       │
├───────────────┬───────────────────────────┬─────────────────┤
│ Pages         │                           │ Properties      │
│               │       PAGE CANVAS         │                 │
│ Cover         │                           │ Text            │
│ Page 1        │       ┌───────────┐       │ Typography      │
│ Page 2        │       │           │       │ Image           │
│ Page 3        │       │   BOOK    │       │ Layout          │
│ Page 4        │       │   PAGE    │       │ Spacing         │
│               │       │           │       │                 │
│ + Add page    │       └───────────┘       │                 │
└───────────────┴───────────────────────────┴─────────────────┘
```

On mobile, adapt the layout rather than simply shrinking the desktop UI.

---

# Page Dimensions

The document must have explicit physical dimensions.

Support at least:

- A5
- A4
- Square

Do not let browser responsiveness change the logical page dimensions.

For example:

```text
Logical document:
148mm × 210mm

Editor:
scaled representation of the same document
```

The same logical page dimensions must be used by the PDF renderer.

---

# Interactive Book Reader

Provide a dedicated reader mode.

The generated book should feel like a real book.

Features:

- cover
- spread view on desktop
- single page on mobile
- page-turn animation
- left/right navigation
- touch/swipe
- keyboard navigation
- page numbers
- fullscreen mode where appropriate
- subtle shadows and depth

The transition should feel physical but remain performant.

Use GPU-friendly animations.

Support reduced-motion preferences.

Do not create an expensive animation system when a simpler implementation provides the same visual quality.

---

# PDF Export

The user must be able to export the same book to PDF.

PDF output must be based on the structured `MemoryBook`.

It should preserve:

- exact page dimensions
- page ordering
- typography
- text layout
- image placement
- crop
- spacing
- backgrounds
- cover
- page numbers
- themes

Support:

- A5
- A4
- Square

The PDF must be suitable for printing.

Do not implement PDF export as a simple screenshot of the browser unless there is a strong technical reason.

Prefer a deterministic print renderer.

---

# Print Fidelity

Pay particular attention to:

- physical page size
- margins
- image resolution
- text wrapping
- font embedding
- image aspect ratios
- page overflow
- missing fonts
- clipping
- crop behavior
- blank pages
- bleed/margin considerations where relevant

Editor layout and print layout should remain visually consistent.

The responsive web viewport must not alter the final print geometry.

---

# Image System

Treat images as first-class assets.

Support:

- upload
- preview
- thumbnails
- high-resolution originals
- cropping
- object positioning
- aspect-ratio preservation
- optimization for browser usage

Do not unnecessarily destroy original files.

Use smaller previews in the editor where practical.

Use appropriate high-resolution assets for PDF output.

Handle:

- portrait photos
- landscape photos
- square photos
- very large images
- missing assets
- deleted assets

---

# Persistence

The book must be persisted.

Implement or extend the repository's existing storage architecture.

Persist:

- book metadata
- page structure
- page order
- element properties
- theme
- image references
- generated content

Provide:

- autosave
- save status
- recovery after refresh
- undo/redo

Avoid making a network request for every tiny interaction.

Debounce or batch persistence.

---

# Responsive Design

The reader must work well on:

- desktop
- tablet
- mobile

The editor should be desktop-first but remain usable on smaller screens.

The page canvas should scale rather than change its logical dimensions.

---

# Performance

Treat performance as a core requirement.

Books may contain dozens of pages and many photographs.

Consider:

- lazy-loading images
- thumbnails
- image optimization
- virtualization for large page lists
- avoiding unnecessary React renders
- memoization where appropriate
- debounced autosave
- GPU-friendly animations
- asynchronous PDF generation
- avoiding full-resolution rendering for every editor page

Do not render every page at maximum resolution simultaneously when unnecessary.

---

# Backend

If a backend already exists, follow its current architecture.

The backend may handle:

- book persistence
- page persistence
- image metadata
- storage references
- AI generation requests
- AI revision requests
- PDF export jobs
- export status

The frontend should primarily manage interaction state.

Do not tightly couple UI components to database implementation.

Do not tightly couple PDF generation to React components.

---

# AI Provider Architecture

Do not tightly couple the application to a single AI provider unless the existing repository already requires it.

Keep AI interactions behind a clear abstraction where practical.

For example:

```text
AI Service
   ↓
generateBook()
reviseBook()
generateCaption()
suggestLayout()
```

This allows future support for different models/providers.

The AI should produce structured data conforming to the document model.

Validate AI-generated output before accepting it into the application.

Do not blindly trust model output.

---

# Error Handling

Handle failures gracefully.

Examples:

- image upload failure
- AI generation failure
- malformed AI output
- PDF generation failure
- missing asset
- network interruption
- expired session
- persistence conflict

The application should preserve as much user work as possible.

Never silently discard a user's edits.

---

# Accessibility

Support:

- keyboard navigation
- visible focus states
- semantic controls
- accessible labels
- usable contrast
- reduced motion
- screen-reader-friendly navigation

The reader should expose meaningful page navigation to assistive technologies.

---

# AI + Human Collaboration

The ideal interaction is:

```text
AI creates
     ↓
User reviews
     ↓
User asks for changes
     ↓
AI revises
     ↓
User makes final adjustments
     ↓
Book is finished
```

Do not make the user manually perform design work that the AI could reasonably do.

Do not make the AI uncontrollably modify the entire book for small requests.

Aim for a collaborative workflow.

---

# Future-Proofing

The architecture should make these future capabilities possible without major redesign:

- public book sharing
- private book links
- image export
- collaborative editing
- physical printing service
- book ordering
- AI-generated cover art
- AI-generated illustrations
- additional page templates
- additional themes
- multiple AI providers
- multilingual books

Do not implement these unless needed, but avoid architectural choices that make them difficult.

---

# Testing

Test the document model:

- create book
- serialize book
- deserialize book
- add page
- delete page
- reorder page
- add element
- remove element
- modify element

Test AI integration:

- valid structured output
- malformed output
- partial output
- failed generation
- revision requests

Test editor:

- editing
- page reorder
- undo/redo
- autosave
- reload/recovery

Test reader:

- first page
- last page
- navigation
- mobile
- keyboard
- swipe

Test PDF:

- page count
- page size
- page order
- text rendering
- image rendering
- cover rendering
- long text
- mixed image orientations
- empty pages

Test edge cases:

- 1-page book
- 50+ page book
- no images
- very long text
- huge image
- missing image
- deleted image
- unusual aspect ratios

---

# Development Process

Before coding:

1. Inspect the repository.
2. Identify the current stack.
3. Identify the frontend architecture.
4. Identify backend architecture.
5. Identify database/storage.
6. Identify existing AI integrations.
7. Identify image handling.
8. Identify testing tools.
9. Identify existing design system/components.

Then choose the smallest architecture that satisfies the requirements.

During development:

- implement incrementally
- keep the existing application working
- reuse components
- use strong typing
- validate data
- test critical transformations
- run the application
- inspect the result
- fix visual and functional issues

Do not rewrite the entire project unless there is a compelling reason.

---

# Agent Behavior

Act as an autonomous engineering agent.

You should:

- inspect before changing
- make pragmatic assumptions
- implement end-to-end
- validate your work
- fix problems you encounter
- preserve working functionality
- avoid unnecessary rewrites

Do not ask clarification questions for minor ambiguities.

Make a reasonable product decision and proceed.

Only stop to ask for clarification when proceeding would create a fundamentally incompatible architecture or destructive change.

---

# Definition of Done

The application is considered complete when a user can perform this full flow:

```text
Open web app
    ↓
Create a memory book
    ↓
Upload photos / memories
    ↓
AI understands the content
    ↓
AI generates the story
    ↓
AI generates page layouts
    ↓
AI creates a complete book
    ↓
User reviews it
    ↓
User edits anything they want
    ↓
User opens the interactive book
    ↓
User turns pages
    ↓
User exports the book as PDF
```

The result must be a real working web application.

The AI-generated book must remain structured and editable.

The interactive reader and PDF exporter must use the same underlying `MemoryBook`.

The key architectural invariant is:

```text
                  MEMORY BOOK
                       │
                       ↓
        ┌──────────────┴──────────────┐
        ↓                             ↓
  Interactive Reader              PDF Export
        ↑                             ↑
        └────────── Renderer ──────────┘
                       ↑
                       │
                  Document Model
                       ↑
                       │
                      AI
```

Build the product, not just the prototype.
