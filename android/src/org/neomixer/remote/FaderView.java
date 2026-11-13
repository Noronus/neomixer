package org.neomixer.remote;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;

/**
 * A fader for a thumb. It moves by as much as the finger does, from wherever it was put down:
 * touching it never makes anything jump, and scrolling past it does not move it.
 */
final class FaderView extends View {
    static final int MAX = 153;
    static final int UNITY = 100;
    /** For this long after the finger moved it, what the mixer reports is the echo of that. */
    private static final long SETTLE_MS = 600;
    private static final long DOUBLE_TAP_MS = 300;

    interface Listener {
        void onFaderMoved(int value);
    }

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();
    private final int touchSlop;
    private final float trackHeight;
    private final float thumbWidth;
    private final float thumbHeight;
    private int color = Ui.VI;
    private int value = UNITY;
    private boolean dimmed;
    private Listener listener;

    private boolean dragging;
    private float downX;
    private float downY;
    private float dragStartValue;
    private long lastTouched;
    private long lastTapUp;

    FaderView(Context context) {
        super(context);
        touchSlop = ViewConfiguration.get(context).getScaledTouchSlop();
        trackHeight = Ui.dp(context, 6);
        thumbWidth = Ui.dp(context, 16);
        thumbHeight = Ui.dp(context, 30);
        setMinimumHeight(Ui.dp(context, 44));
    }

    void setListener(Listener listener) {
        this.listener = listener;
    }

    void setColor(int color) {
        this.color = color;
        invalidate();
    }

    void setDimmed(boolean dimmed) {
        if (this.dimmed != dimmed) {
            this.dimmed = dimmed;
            invalidate();
        }
    }

    int getValue() {
        return value;
    }

    /** What the mixer says the value is. Ignored while a finger is on it, or was a moment ago. */
    void showValue(int newValue) {
        if (dragging || SystemClock.uptimeMillis() - lastTouched < SETTLE_MS) {
            return;
        }
        if (newValue != value) {
            value = Math.max(0, Math.min(newValue, MAX));
            invalidate();
        }
    }

    @Override
    protected void onMeasure(int widthSpec, int heightSpec) {
        setMeasuredDimension(MeasureSpec.getSize(widthSpec), resolveSize(Ui.dp(getContext(), 44), heightSpec));
    }

    private float usable() {
        return Math.max(1, getWidth() - thumbWidth);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float middle = getHeight() / 2f;
        float start = thumbWidth / 2;
        float end = getWidth() - thumbWidth / 2;
        float position = start + usable() * value / MAX;
        float unity = start + usable() * UNITY / MAX;
        int shown = dimmed ? Ui.FAINT : color;

        paint.setColor(Ui.LINE);
        rect.set(start, middle - trackHeight / 2, end, middle + trackHeight / 2);
        canvas.drawRoundRect(rect, trackHeight / 2, trackHeight / 2, paint);

        paint.setColor(Ui.alpha(shown, dragging ? 1f : 0.8f));
        rect.set(start, middle - trackHeight / 2, position, middle + trackHeight / 2);
        canvas.drawRoundRect(rect, trackHeight / 2, trackHeight / 2, paint);

        // 0 dB
        paint.setColor(Ui.DIM);
        canvas.drawRect(unity - 1, middle - trackHeight * 1.6f, unity + 1, middle - trackHeight * 0.9f, paint);
        canvas.drawRect(unity - 1, middle + trackHeight * 0.9f, unity + 1, middle + trackHeight * 1.6f, paint);

        paint.setColor(dragging ? 0xffffffff : Ui.TEXT);
        rect.set(position - thumbWidth / 2, middle - thumbHeight / 2, position + thumbWidth / 2, middle + thumbHeight / 2);
        canvas.drawRoundRect(rect, thumbWidth / 3, thumbWidth / 3, paint);
        paint.setColor(shown);
        canvas.drawRect(position - 1, middle - thumbHeight / 4, position + 1, middle + thumbHeight / 4, paint);
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downX = event.getX();
                downY = event.getY();
                dragStartValue = value;
                dragging = false;
                return true;

            case MotionEvent.ACTION_MOVE:
                float dx = event.getX() - downX;
                if (!dragging) {
                    if (Math.abs(dx) < touchSlop || Math.abs(dx) < Math.abs(event.getY() - downY)) {
                        return true;
                    }
                    // It is this fader that is meant now, not the list around it
                    dragging = true;
                    downX = event.getX();
                    dx = 0;
                    getParent().requestDisallowInterceptTouchEvent(true);
                }
                moveTo(Math.round(dragStartValue + dx / usable() * MAX));
                return true;

            case MotionEvent.ACTION_UP:
                if (!dragging) {
                    long now = SystemClock.uptimeMillis();
                    if (now - lastTapUp < DOUBLE_TAP_MS) {
                        moveTo(UNITY);
                        lastTapUp = 0;
                    } else {
                        lastTapUp = now;
                    }
                }
                dragging = false;
                invalidate();
                return true;

            case MotionEvent.ACTION_CANCEL:
                dragging = false;
                invalidate();
                return true;

            default:
                return super.onTouchEvent(event);
        }
    }

    private void moveTo(int newValue) {
        newValue = Math.max(0, Math.min(newValue, MAX));
        lastTouched = SystemClock.uptimeMillis();
        if (newValue == value) {
            return;
        }
        value = newValue;
        invalidate();
        if (listener != null) {
            listener.onFaderMoved(value);
        }
    }
}
