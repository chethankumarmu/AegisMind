# Contributing to AegisMind

First off, thank you for considering contributing to AegisMind! It's people like you that make AegisMind such a great tool.

## Where do I go from here?

If you've noticed a bug or have a feature request, make sure to check our [Issues](../../issues) to see if someone else in the community has already created a ticket. If not, go ahead and [make one](../../issues/new/choose)!

## Fork & create a branch

If this is something you think you can fix, then fork AegisMind and create a branch with a descriptive name.

## Get the test suite running

Make sure you're using `pnpm` for the frontend and `uv` for the backend.

### Frontend
1. Navigate to the frontend directory: `cd apps/lens`
2. Install dependencies: `pnpm install`
3. Start the dev server: `pnpm dev`

### Backend
1. Navigate to the backend directory.
2. Ensure you have `uv` installed.
3. Run the development server: `uv run uvicorn aegismind_core.app:app --reload --port 8000`

## Implement your fix or feature

At this point, you're ready to make your changes! Feel free to ask for help; everyone is a beginner at first 😸

## Make a Pull Request

At this point, you should switch back to your master branch and make sure it's up to date with AegisMind's master branch:

```sh
git remote add upstream git@github.com:Ayush-Arun/AegisMind.git
git checkout main
git pull upstream main
```

Then update your feature branch from your local copy of main, and push it!

```sh
git checkout 325-add-new-feature
git rebase main
git push --set-upstream origin 325-add-new-feature
```

Finally, go to GitHub and [make a Pull Request](../../pulls) with a description of what you've done.
