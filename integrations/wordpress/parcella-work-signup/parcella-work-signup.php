<?php
/**
 * Plugin Name: Parcella Work Session Signup
 * Description: Reference connector for Parcella's public signup API. Renders a work-session signup form (shortcode [parcella_work_signup]) that reads live sessions/parcels from Parcella and submits signups back to it. All calls to Parcella happen server-side in PHP, so the API token never reaches the visitor's browser.
 * Version: 1.0.0
 * License: AGPL-3.0-or-later
 * Text Domain: parcella-work-signup
 *
 * This plugin is intentionally a thin client: it contains no business
 * logic (capacity checks, matching, validation) -- all of that lives in
 * Parcella itself, behind the same API contract any other CMS connector
 * would use. See docs/module-public-api.md in the Parcella repository
 * for the full contract and the reasoning behind this split.
 */

if (!defined('ABSPATH')) {
    exit; // No direct access.
}

define('PARCELLA_SIGNUP_VERSION', '1.0.0');
define('PARCELLA_SIGNUP_OPTION_BASE_URL', 'parcella_signup_base_url');
define('PARCELLA_SIGNUP_OPTION_API_TOKEN', 'parcella_signup_api_token');

// Loads translations from languages/parcella-work-signup-{locale}.mo,
// e.g. languages/parcella-work-signup-de_DE.mo for a German site. Falls
// back to the English strings in this file automatically if no
// matching .mo file exists -- so a project in yet another language just
// needs to drop in its own .mo/.po pair, no code change required.
add_action('plugins_loaded', function () {
    load_plugin_textdomain('parcella-work-signup', false, dirname(plugin_basename(__FILE__)) . '/languages');
});

// ---------------------------------------------------------------------------
// Settings page (Settings -> Parcella)
// ---------------------------------------------------------------------------

add_action('admin_menu', function () {
    add_options_page(
        __('Parcella Connector', 'parcella-work-signup'),
        __('Parcella Connector', 'parcella-work-signup'),
        'manage_options',
        'parcella-work-signup',
        'parcella_signup_render_settings_page'
    );
});

add_action('admin_init', function () {
    register_setting('parcella_signup_settings', PARCELLA_SIGNUP_OPTION_BASE_URL, [
        'sanitize_callback' => function ($value) {
            return untrailingslashit(esc_url_raw(trim($value)));
        },
    ]);
    register_setting('parcella_signup_settings', PARCELLA_SIGNUP_OPTION_API_TOKEN, [
        'sanitize_callback' => 'sanitize_text_field',
    ]);
});

function parcella_signup_render_settings_page() {
    if (!current_user_can('manage_options')) {
        return;
    }
    ?>
    <div class="wrap">
        <h1><?php esc_html_e('Parcella Connector', 'parcella-work-signup'); ?></h1>
        <p>
            <?php esc_html_e(
                'Connects this site to your Parcella installation\'s public signup API. Find the base URL and API token on the "Integrations" page in Parcella\'s admin area (Administration -> Integrations).',
                'parcella-work-signup'
            ); ?>
        </p>
        <form method="post" action="options.php">
            <?php settings_fields('parcella_signup_settings'); ?>
            <table class="form-table">
                <tr>
                    <th scope="row">
                        <label for="parcella_base_url"><?php esc_html_e('Parcella base URL', 'parcella-work-signup'); ?></label>
                    </th>
                    <td>
                        <input type="url" id="parcella_base_url" name="<?php echo esc_attr(PARCELLA_SIGNUP_OPTION_BASE_URL); ?>"
                               value="<?php echo esc_attr(get_option(PARCELLA_SIGNUP_OPTION_BASE_URL, '')); ?>"
                               class="regular-text" placeholder="https://parcella.example-club.org">
                    </td>
                </tr>
                <tr>
                    <th scope="row">
                        <label for="parcella_api_token"><?php esc_html_e('API token', 'parcella-work-signup'); ?></label>
                    </th>
                    <td>
                        <input type="password" id="parcella_api_token" name="<?php echo esc_attr(PARCELLA_SIGNUP_OPTION_API_TOKEN); ?>"
                               value="<?php echo esc_attr(get_option(PARCELLA_SIGNUP_OPTION_API_TOKEN, '')); ?>"
                               class="regular-text" autocomplete="off">
                        <p class="description">
                            <?php esc_html_e('Only used for the write (signup submission) call, sent server-side. Never exposed to visitors.', 'parcella-work-signup'); ?>
                        </p>
                    </td>
                </tr>
            </table>
            <?php submit_button(); ?>
        </form>
        <p>
            <?php esc_html_e('Place the form anywhere with the shortcode:', 'parcella-work-signup'); ?>
            <code>[parcella_work_signup]</code>
        </p>
    </div>
    <?php
}

// ---------------------------------------------------------------------------
// Helpers: talk to Parcella
// ---------------------------------------------------------------------------

function parcella_signup_base_url() {
    return get_option(PARCELLA_SIGNUP_OPTION_BASE_URL, '');
}

/**
 * Fetches upcoming sessions / parcels, cached briefly in a transient so a
 * busy page doesn't hit Parcella on every single page view. Read-only,
 * unauthenticated calls -- matches Parcella's public GET endpoints.
 */
function parcella_signup_fetch_json($path, $cache_key, $cache_seconds) {
    $cached = get_transient($cache_key);
    if ($cached !== false) {
        return $cached;
    }

    $base_url = parcella_signup_base_url();
    if (empty($base_url)) {
        return null;
    }

    $response = wp_remote_get($base_url . $path, ['timeout' => 8]);
    if (is_wp_error($response) || wp_remote_retrieve_response_code($response) !== 200) {
        return null;
    }

    $data = json_decode(wp_remote_retrieve_body($response), true);
    if (!is_array($data)) {
        return null;
    }

    set_transient($cache_key, $data, $cache_seconds);
    return $data;
}

function parcella_signup_fetch_sessions() {
    return parcella_signup_fetch_json('/api/v1/public/work-sessions/upcoming', 'parcella_signup_sessions', 60);
}

function parcella_signup_fetch_parcels() {
    return parcella_signup_fetch_json('/api/v1/public/parcels', 'parcella_signup_parcels', 3600);
}

/**
 * Submits a signup. Server-side POST, so the API token is attached here
 * and never sent to (or seen by) the visitor's browser.
 */
function parcella_signup_submit($payload) {
    $base_url = parcella_signup_base_url();
    $token = get_option(PARCELLA_SIGNUP_OPTION_API_TOKEN, '');
    if (empty($base_url) || empty($token)) {
        return ['error' => __('This form is not fully configured yet. Please contact the site administrator.', 'parcella-work-signup')];
    }

    $response = wp_remote_post($base_url . '/api/v1/public/work-sessions/signup', [
        'timeout' => 10,
        'headers' => [
            'Content-Type' => 'application/json',
            'X-Parcella-API-Token' => $token,
        ],
        'body' => wp_json_encode($payload),
    ]);

    if (is_wp_error($response)) {
        return ['error' => __('Could not reach Parcella. Please try again later.', 'parcella-work-signup')];
    }

    $status = wp_remote_retrieve_response_code($response);
    $body = json_decode(wp_remote_retrieve_body($response), true);

    if ($status === 401) {
        return ['error' => __('This form is not correctly configured (invalid API token). Please contact the site administrator.', 'parcella-work-signup')];
    }
    if ($status === 429) {
        return ['error' => __('Too many submissions right now. Please try again in a little while.', 'parcella-work-signup')];
    }
    if ($status === 404 && is_array($body)) {
        return ['error' => __('That parcel number was not found. Please check it and try again.', 'parcella-work-signup')];
    }
    if ($status !== 200 || !is_array($body)) {
        return ['error' => __('Something went wrong submitting your signup. Please try again later.', 'parcella-work-signup')];
    }

    return ['result' => $body];
}

// ---------------------------------------------------------------------------
// Shortcode: [parcella_work_signup]
// ---------------------------------------------------------------------------

add_shortcode('parcella_work_signup', 'parcella_signup_render_shortcode');

function parcella_signup_render_shortcode($atts) {
    $feedback = null;

    if (
        isset($_POST['parcella_signup_nonce'])
        && wp_verify_nonce(sanitize_text_field(wp_unslash($_POST['parcella_signup_nonce'])), 'parcella_signup_submit')
    ) {
        $session_ids = isset($_POST['session_ids']) && is_array($_POST['session_ids'])
            ? array_map('sanitize_text_field', wp_unslash($_POST['session_ids']))
            : [];

        $payload = [
            'parcel_number' => sanitize_text_field(wp_unslash($_POST['parcel_number'] ?? '')),
            'name' => sanitize_text_field(wp_unslash($_POST['parcella_signup_name'] ?? '')),
            'remarks' => sanitize_textarea_field(wp_unslash($_POST['remarks'] ?? '')),
            'session_ids' => $session_ids,
            // Honeypot: a hidden field real visitors never fill in (see
            // the CSS below). Forwarded as-is; Parcella itself decides
            // what to do with a filled-in value.
            'website' => sanitize_text_field(wp_unslash($_POST['website'] ?? '')),
        ];

        if (empty($payload['parcel_number']) || empty($payload['session_ids'])) {
            $feedback = ['error' => __('Please provide a parcel number and select at least one session.', 'parcella-work-signup')];
        } else {
            $feedback = parcella_signup_submit($payload);
            // Signup lists may have changed capacity -- drop the cached
            // session list so the form reflects it on next render.
            delete_transient('parcella_signup_sessions');
        }
    }

    $sessions = parcella_signup_fetch_sessions();
    $parcels = parcella_signup_fetch_parcels();

    ob_start();
    ?>
    <div class="parcella-work-signup">
        <?php if ($sessions === null || $parcels === null): ?>
            <p><?php esc_html_e('The signup form is temporarily unavailable. Please try again later.', 'parcella-work-signup'); ?></p>
        <?php else: ?>

            <?php if ($feedback && isset($feedback['error'])): ?>
                <div class="parcella-signup-message parcella-signup-error"><?php echo esc_html($feedback['error']); ?></div>
            <?php elseif ($feedback && isset($feedback['result'])): ?>
                <?php
                $any_accepted = false;
                $any_rejected = false;
                foreach ($feedback['result']['results'] as $r) {
                    if ($r['accepted']) { $any_accepted = true; } else { $any_rejected = true; }
                }
                ?>
                <?php if ($any_accepted): ?>
                    <div class="parcella-signup-message parcella-signup-success">
                        <?php esc_html_e('Thank you, your signup has been received.', 'parcella-work-signup'); ?>
                    </div>
                <?php endif; ?>
                <?php if ($any_rejected): ?>
                    <div class="parcella-signup-message parcella-signup-error">
                        <?php esc_html_e('Some of the selected sessions could not be booked (likely full). Please choose another date for those.', 'parcella-work-signup'); ?>
                    </div>
                <?php endif; ?>
            <?php endif; ?>

            <?php if (empty($sessions)): ?>
                <p><?php esc_html_e('There are currently no upcoming work sessions open for signup.', 'parcella-work-signup'); ?></p>
            <?php else: ?>
            <form method="post" class="parcella-signup-form">
                <?php wp_nonce_field('parcella_signup_submit', 'parcella_signup_nonce'); ?>

                <p>
                    <label for="parcella-name"><?php esc_html_e('Name', 'parcella-work-signup'); ?></label><br>
                    <!--
                        Field is deliberately NOT called "name": WordPress
                        treats "name" as a reserved core query variable
                        (used to look up a post/page by slug). A POST
                        field literally called "name" gets picked up by
                        WP::parse_request() and WordPress tries to find a
                        page with that slug instead of just rendering
                        this page's own content -- which 404s the moment
                        this field is non-empty. Namespacing it avoids
                        the collision entirely.
                    -->
                    <input type="text" id="parcella-name" name="parcella_signup_name">
                </p>

                <p>
                    <label for="parcella-parcel"><?php esc_html_e('Parcel number', 'parcella-work-signup'); ?> *</label><br>
                    <select id="parcella-parcel" name="parcel_number" required>
                        <option value=""><?php esc_html_e('Please choose...', 'parcella-work-signup'); ?></option>
                        <?php foreach ($parcels as $parcel): ?>
                            <option value="<?php echo esc_attr($parcel['plot_number']); ?>">
                                <?php echo esc_html($parcel['plot_number']); ?>
                            </option>
                        <?php endforeach; ?>
                    </select>
                </p>

                <p>
                    <?php esc_html_e('I would like to sign up for the following work sessions:', 'parcella-work-signup'); ?><br>
                    <?php foreach ($sessions as $session): ?>
                        <label style="display:block;">
                            <input type="checkbox" name="session_ids[]" value="<?php echo esc_attr($session['id']); ?>">
                            <?php
                            echo esc_html(
                                sprintf(
                                    /* translators: 1: date, 2: start time, 3: end time, 4: session title */
                                    __('%1$s, %2$s - %3$s %4$s', 'parcella-work-signup'),
                                    date_i18n(get_option('date_format'), strtotime($session['date'])),
                                    $session['time_from'] ?? '',
                                    $session['time_until'] ?? '',
                                    $session['title']
                                )
                            );
                            if (isset($session['spots_left']) && $session['spots_left'] !== null) {
                                echo ' ' . esc_html(sprintf(
                                    /* translators: %d: number of remaining spots */
                                    _n('(%d spot left)', '(%d spots left)', $session['spots_left'], 'parcella-work-signup'),
                                    $session['spots_left']
                                ));
                            }
                            ?>
                        </label>
                    <?php endforeach; ?>
                </p>

                <p>
                    <label for="parcella-remarks"><?php esc_html_e('Remarks or individual session requests', 'parcella-work-signup'); ?></label><br>
                    <textarea id="parcella-remarks" name="remarks" rows="3"></textarea>
                </p>

                <!--
                    Honeypot field: hidden from real visitors via CSS and
                    kept out of the tab order / accessibility tree, but
                    still present in the markup for simple bots that fill
                    in every field they find.
                -->
                <p class="parcella-signup-hp" aria-hidden="true">
                    <label for="parcella-website"><?php esc_html_e('Leave this field empty', 'parcella-work-signup'); ?></label>
                    <input type="text" id="parcella-website" name="website" tabindex="-1" autocomplete="off">
                </p>

                <p>
                    <button type="submit"><?php esc_html_e('Sign up', 'parcella-work-signup'); ?></button>
                </p>
            </form>
            <?php endif; ?>
        <?php endif; ?>
    </div>
    <style>
        .parcella-signup-hp { position: absolute; left: -9999px; }
        .parcella-signup-message { padding: 0.75em 1em; margin-bottom: 1em; border-radius: 4px; }
        .parcella-signup-success { background: #e6f4ea; color: #1e4620; }
        .parcella-signup-error { background: #fdecea; color: #611a15; }
    </style>
    <?php
    return ob_get_clean();
}
