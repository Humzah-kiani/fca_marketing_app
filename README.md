# Direct Social Publisher for Streamlit

This starter module calls official platform APIs directly. It does not use Buffer, Publer, or Ayrshare.

## Included
- Instagram: single image or 2–10 image carousel
- Facebook Page: single image or multi-photo Page post
- LinkedIn: single image or multi-image post
- Streamlit UI with ordered public image URLs, previews, caption, and explicit publish confirmation

## 1. Install

```bash
pip install streamlit requests
```

## 2. Add secrets

Create `.streamlit/secrets.toml` (never commit this file):

```toml
# Meta / Facebook / Instagram
META_GRAPH_VERSION = "v26.0"
META_ACCESS_TOKEN = "YOUR_META_ACCESS_TOKEN"
INSTAGRAM_USER_ID = "YOUR_INSTAGRAM_PROFESSIONAL_ACCOUNT_ID"
FACEBOOK_PAGE_ACCESS_TOKEN = "YOUR_FACEBOOK_PAGE_ACCESS_TOKEN"
FACEBOOK_PAGE_ID = "YOUR_FACEBOOK_PAGE_ID"

# LinkedIn
LINKEDIN_VERSION = "202608"
LINKEDIN_ACCESS_TOKEN = "YOUR_LINKEDIN_ACCESS_TOKEN"
LINKEDIN_AUTHOR_URN = "urn:li:person:YOUR_PERSON_ID"
# For a company page instead use: urn:li:organization:YOUR_ORGANIZATION_ID
```

Versions are configurable. Confirm supported/current versions in the official docs before production use.

## 3. Add to your existing Streamlit app

Copy `direct_social_publisher.py` into the same project directory as your `app.py`, then:

```python
from direct_social_publisher import render_direct_social_publisher

# Put this in a page, tab, or branch in your existing app:
render_direct_social_publisher()
```

Example sidebar integration:

```python
page = st.sidebar.radio("Workspace", ["Dashboard", "Direct Social Publisher"])
if page == "Direct Social Publisher":
    render_direct_social_publisher()
```

## 4. Image URL requirements

This UI accepts direct public HTTPS URLs. The URL must resolve to the actual image bytes (not a webpage or a private login page). Instagram and Facebook fetch the media from the URL. LinkedIn's backend downloads it and uploads the bytes to LinkedIn.

If you want users to upload local files from Streamlit, add a storage provider such as Cloudinary or Supabase Storage and pass the resulting public URLs into the publishing functions. This starter does not implement storage.

## 5. Developer setup and permissions

### Meta (Instagram + Facebook Pages)
- Create a Meta developer app and add the applicable products/use cases.
- Instagram publishing requires an eligible Instagram professional account and the required permissions/access for the chosen login setup. A personal Instagram account is not sufficient for this publishing flow.
- Facebook publishing requires a Facebook Page and a Page access token with the necessary publishing permissions.
- Common permission areas include `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`; exact requirements vary with the selected Meta login product and app mode.
- App review / business verification may be required before other users or non-role accounts can use the integration.
- Get the actual Instagram professional account ID and Facebook Page ID from the official account/page discovery flow or Meta tools; do not guess them.

### LinkedIn
- Create a LinkedIn developer app and request access to the applicable Share on LinkedIn / Community Management products.
- Use a valid OAuth access token with `w_member_social` for member posting; organization posting requires the appropriate organization permission and the user must have a qualifying Page role.
- `LINKEDIN_AUTHOR_URN` is the author URN, such as `urn:li:person:123` or `urn:li:organization:123`.
- LinkedIn Marketing APIs require a versioned `LinkedIn-Version` header. The version in secrets is a configurable example; use a currently supported version for your app.

Official docs:
- Instagram Platform: https://developers.facebook.com/docs/instagram-platform/
- Meta Graph API: https://developers.facebook.com/docs/graph-api/
- Facebook Pages API: https://developers.facebook.com/docs/pages-api/
- LinkedIn Posts API: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
- LinkedIn Images API: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api

## 6. Important production notes
- This is a ready-to-integrate starter, not a complete OAuth product. Tokens are configured in secrets and do not auto-refresh. Implement OAuth, refresh/renewal where available, per-user encrypted token storage, rate-limit handling, audit logs, and duplicate-post protection before opening this to other users.
- Publishing is immediate. Scheduled publishing needs a durable job queue and a worker (e.g., APScheduler for a small single-instance prototype, or Celery/RQ + Redis for production). Do not rely on Streamlit session state for durable schedules.
- Platform APIs can change. Carousel support, image count, file requirements, permissions, app review, and account eligibility are controlled by each platform.
- This module supports image URLs only, not video or mixed-media carousels.
- Meta's carousel media processing can take time. If it is still processing after the polling window, the module reports that rather than publishing an unready container.
- Never commit access tokens to Git or expose them to browser-side code.
- Before using on real accounts, test each platform with a test account/Page and verify the result on the platform itself.
