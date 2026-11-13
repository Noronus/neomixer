package org.neomixer.remote;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

/** The mixer's colors, and the few things every view here is made of. */
final class Ui {
    static final int BG = 0xff0e1117;
    static final int PANEL = 0xff141821;
    static final int STRIP = 0xff1a1f2a;
    static final int STRIP_HIGH = 0xff212734;
    static final int LINE = 0xff2a3140;
    static final int TEXT = 0xffe6e9f0;
    static final int DIM = 0xff8b93a7;
    static final int FAINT = 0xff5b6375;
    static final int HI = 0xff38bdf8;
    static final int VI = 0xffa78bfa;
    static final int A = 0xff34d399;
    static final int B = 0xfffb923c;
    static final int APP = 0xfff472b6;
    static final int MUTE = 0xffef4444;

    private Ui() {
    }

    static int colorOf(String type) {
        switch (type) {
            case "hi":
                return HI;
            case "vi":
                return VI;
            case "a":
                return A;
            case "b":
                return B;
            default:
                return APP;
        }
    }

    static int dp(Context context, float value) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value, context.getResources().getDisplayMetrics()));
    }

    static int alpha(int color, float alpha) {
        return (Math.round(alpha * 255) << 24) | (color & 0x00ffffff);
    }

    static GradientDrawable rounded(Context context, int fill, float radiusDp) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(fill);
        shape.setCornerRadius(dp(context, radiusDp));
        return shape;
    }

    static GradientDrawable outlined(Context context, int fill, int stroke, float radiusDp) {
        GradientDrawable shape = rounded(context, fill, radiusDp);
        shape.setStroke(Math.max(1, dp(context, 1)), stroke);
        return shape;
    }

    static TextView text(Context context, float sizeSp, int color, boolean bold) {
        TextView view = new TextView(context);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp);
        view.setTextColor(color);
        if (bold) {
            view.setTypeface(Typeface.DEFAULT_BOLD);
        }
        return view;
    }

    static String db(int volume) {
        if (volume <= 0) {
            return "-∞ dB";
        }
        // pulse volumes are cubic
        double db = 60 * Math.log10(volume / 100.0);
        if (Math.abs(db) < 0.05) {
            return "0.0 dB";
        }
        return String.format(java.util.Locale.US, "%+.1f dB", db);
    }

    /** Lays its children out in rows, as many in each as fit. */
    static final class FlowLayout extends ViewGroup {
        private final int gap;

        FlowLayout(Context context, int gapDp) {
            super(context);
            gap = dp(context, gapDp);
        }

        @Override
        protected void onMeasure(int widthSpec, int heightSpec) {
            int width = MeasureSpec.getSize(widthSpec) - getPaddingLeft() - getPaddingRight();
            int x = 0;
            int y = 0;
            int rowHeight = 0;
            for (int i = 0; i < getChildCount(); i++) {
                View child = getChildAt(i);
                if (child.getVisibility() == GONE) {
                    continue;
                }
                child.measure(MeasureSpec.makeMeasureSpec(width, MeasureSpec.AT_MOST), MeasureSpec.UNSPECIFIED);
                if (x > 0 && x + child.getMeasuredWidth() > width) {
                    x = 0;
                    y += rowHeight + gap;
                    rowHeight = 0;
                }
                x += child.getMeasuredWidth() + gap;
                rowHeight = Math.max(rowHeight, child.getMeasuredHeight());
            }
            setMeasuredDimension(MeasureSpec.getSize(widthSpec), y + rowHeight + getPaddingTop() + getPaddingBottom());
        }

        @Override
        protected void onLayout(boolean changed, int left, int top, int right, int bottom) {
            int width = right - left - getPaddingLeft() - getPaddingRight();
            int x = 0;
            int y = 0;
            int rowHeight = 0;
            for (int i = 0; i < getChildCount(); i++) {
                View child = getChildAt(i);
                if (child.getVisibility() == GONE) {
                    continue;
                }
                if (x > 0 && x + child.getMeasuredWidth() > width) {
                    x = 0;
                    y += rowHeight + gap;
                    rowHeight = 0;
                }
                child.layout(getPaddingLeft() + x, getPaddingTop() + y,
                        getPaddingLeft() + x + child.getMeasuredWidth(), getPaddingTop() + y + child.getMeasuredHeight());
                x += child.getMeasuredWidth() + gap;
                rowHeight = Math.max(rowHeight, child.getMeasuredHeight());
            }
        }
    }
}
