# Collaboration Rules with Project Owner

1. **Ask & Clarify First Before Implementing**:
   - Whenever a feature, change, or user idea is requested:
     - If the requirements are not 100% specific, or if you can improve upon the owner's idea with better alternatives, **DO NOT assume or jump straight into modifying code**.
     - **Ask the owner first** using the interactive question tool.
     - Present clear, numbered choices with the recommended option first, but always provide an option for the owner to write down their own custom decision or reasoning.
     - Wait for the owner's decision before implementing.

2. **Proactive Professional Suggestions**:
   - The owner explicitly wants improvement and constructive suggestions: *"the most important is you have to make the improvement on my idea — do not agree with me everytime, and suggest me good ideas."*
   - Suggest better UX, cleaner patterns, and edge-case protections, presenting them as selectable choices.

3. **Workflow & Deployment**:
   - Always commit and push directly to `main` on GitHub so the owner can run `git-pull.bat`.
   - Keep `daikin_stock.xlsx` gitignored (real business data).
   - Run JS syntax checks (`node -e ...`) and Python compile checks before committing.
   - Packaging to `.exe` (PyInstaller) is deferred until all features are completely finished and approved by the owner.
