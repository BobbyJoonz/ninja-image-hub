// ninja:refine-no-stale-extract
// In a reused chat the previous image is already in the DOM. Falling back to "last image"
// returns the OLD image and makes a refinement look successful. Wait for a genuinely new
// image container; if none appears, fail with a typed error so the Hub can edit instead.
  } else if (context.reuseChat) {
    for (let waited = 0; waited < 30000; waited += 1000) {
      await p.waitForTimeout(1000);
      const fresh = await p.evaluate((prevIds) => {
        const all = Array.from(document.querySelectorAll('div[id^="image-"]'))
          .filter(el => !(el.closest && el.closest('[data-message-author-role="user"], [data-content-search-unit-key$=":user"], [data-user-message-bubble]')))
          .map(el => el.id)
          .filter(id => id && !prevIds.includes(id));
        return all.length > 0 ? all[all.length - 1] : null;
      }, beforeState.imageIds || []).catch(() => null);
      if (fresh) { newImageId = fresh; break; }
    }
    if (!newImageId) {
      const err = new Error('No new image was generated in this conversation (stale image only).');
      err.code = 'no_new_image';
      throw err;
    }
    console.log(`[extract] Targeting new image container after reuse wait: ${newImageId}`);
  } else {
    console.log('[extract] No new image container found vs beforeState — falling back to last in DOM');
  }
