# Publish from GitHub

[**Deploy this GitHub repository to Render**](https://render.com/deploy?repo=https://github.com/aliitworx-source/resource-management)

## First publication

1. Open the link and sign in to Render with GitHub.
2. If requested, connect GitHub and grant Render access to `aliitworx-source/resource-management`. Private repositories require this one-time permission.
3. Render reads the included `render.yaml`. Review the paid Starter service and 1 GB persistent disk price, then click **Deploy Blueprint**.
4. Wait for **Live**, then open the HTTPS URL shown on the service page.
5. Log in with username `admin`. View the generated password under that service's **Environment → APP_ADMIN_PASSWORD**. Keep it private.

You do not need to provide this chat with an API key. Render generates the password and session key, installs Python dependencies, starts the server, checks its health, and mounts the persistent database disk.

## Publish later changes

Push or merge your changes into the repository's `main` branch. Render automatically deploys that branch. GitHub Actions runs tests separately; its result is visible in the repository's **Actions** tab. Review those checks before merging a pull request. The Blueprint uses commit-triggered deployment; it does not wait for the GitHub check result.

For an existing Render service created from an earlier blueprint, sync the updated Blueprint in Render to adopt its settings. You can also select **Manual Deploy → Deploy latest commit**. Do not create a second service if you want to keep using the original database.

## Important details

- The application includes sample people, projects and allocations for evaluation. The administration section has been removed; login and role permissions remain.
- Password generation initializes a new database only. Existing installations retain their existing login password.
- The database is stored at `/var/data/resource.db` on the persistent disk. An ephemeral/free service would lose local SQLite data; the included persistent setup requires paid Render resources.
- This workflow publishes a running web application. GitHub Pages only serves static files and cannot host the Flask backend.
- Future production use still requires the release work listed in [README.md](README.md#scope-and-release-readiness).
