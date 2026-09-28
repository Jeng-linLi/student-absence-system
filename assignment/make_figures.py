# -*- coding: utf-8 -*-
"""生成作業與簡報用的流程圖 / 概念圖"""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = os.path.dirname(os.path.abspath(__file__))
BLUE, INK, GREY = '#0F6CBD', '#201F1E', '#6B6B6B'
GREEN, AMBER, PURPLE, RED = '#0E7A3D', '#B8860B', '#6C4AB6', '#B10E1C'


def box(ax, x, y, w, h, text, fc, tc='#201F1E', fs=10.5, bold=True, lw=1.6):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle='round,pad=0.06,rounding_size=0.12',
                                linewidth=lw, edgecolor=fc, facecolor='white', zorder=3))
    weight = 'bold' if bold else 'normal'
    ax.text(x, y, text, ha='center', va='center', fontsize=fs, color=tc,
            fontweight=weight, zorder=4, linespacing=1.45)


def arrow(ax, x1, y1, x2, y2, color=GREY, style='-|>', lw=1.6, ls='-'):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=16, linewidth=lw,
                                 color=color, linestyle=ls, zorder=2,
                                 shrinkA=2, shrinkB=2))


def base(ax, w=16, h=9.4):
    ax.set_xlim(0, w); ax.set_ylim(0, h); ax.axis('off')
    return ax


# ---------------------------------------------------------------- 圖一 流程
def fig_workflow():
    fig, ax = plt.subplots(figsize=(13.2, 8.0), dpi=170)
    base(ax)
    ax.text(0.4, 9.0, 'Figure 1 · How a SAAS request flows',
            fontsize=13, fontweight='bold', color=INK, va='center')

    box(ax, 8.0, 8.05, 6.6, 0.78, 'Student submits ONE request\n(category · dates · reason · evidence)', BLUE, fs=11)
    arrow(ax, 8.0, 7.62, 8.0, 7.18)

    box(ax, 8.0, 6.62, 8.6, 0.92,
        'System resolves enrolments × assessment calendar\n→ one conflict record per affected quiz / midterm / final / lab',
        PURPLE, fs=10)
    arrow(ax, 8.0, 6.14, 8.0, 5.72)

    box(ax, 8.0, 5.28, 4.6, 0.72, 'Route by policy (configuration)', INK, fs=10.5)

    # 三條路由
    arrow(ax, 6.9, 4.92, 3.4, 4.52)
    arrow(ax, 8.0, 4.92, 8.0, 4.52)
    arrow(ax, 9.1, 4.92, 12.6, 4.52)

    box(ax, 3.1, 3.72, 3.9, 1.55, 'CASE A · Medical\n\n→ Affected instructors\ndecide', GREEN, fs=10)
    box(ax, 8.0, 3.72, 3.9, 1.55, 'CASE B · Official activity\n\n→ Roster auto-verified\ninstructors notified only', AMBER, fs=10)
    box(ax, 12.9, 3.72, 4.3, 1.55, 'CASE C · Absence > 7 days\n\n→ Programme Office\n→ Registry → instructors', RED, fs=10)

    arrow(ax, 3.1, 2.92, 3.1, 2.42)
    arrow(ax, 8.0, 2.92, 8.0, 2.42)
    arrow(ax, 12.9, 2.92, 12.9, 2.42)
    ax.plot([3.1, 12.9], [2.32, 2.32], color=GREY, lw=1.6)
    arrow(ax, 8.0, 2.32, 8.0, 2.02)

    box(ax, 8.0, 1.42, 10.4, 1.22,
        'Instructor selects one accommodation per conflict:\n'
        'excused · make-up assessment · alternative assignment · weight redistribution · rejection (with reasons)',
        BLUE, fs=10)

    arrow(ax, 8.0, 0.79, 8.0, 0.5)
    box(ax, 8.0, 0.28, 11.6, 0.5,
        'Student sees outcome, new deadline and make-up date · notifications via email / Teams / push · every step audited',
        INK, fs=9.5, bold=False, lw=1.2)

    fig.tight_layout()
    p = os.path.join(OUT, 'fig1_workflow.png')
    fig.savefig(p, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return p


# ---------------------------------------------------------------- 圖二 S 曲線
def fig_scurve():
    fig, ax = plt.subplots(figsize=(8.6, 5.4), dpi=170)
    ax.set_xlim(0, 10); ax.set_ylim(0, 6.6)

    def s(x, k=1.0, x0=4.4, ymax=5.6):
        return ymax / (1 + pow(2.718, -k * (x - x0)))

    xs = [i / 40 * 10 for i in range(401)]
    ax.plot(xs, [s(x, 1.15, 3.4, 5.5) for x in xs], color=GREY, lw=2.4,
            label='Rule-based workflow automation (mature)')
    ax.plot(xs, [s(x, 1.15, 7.4, 5.9) for x in xs], color=BLUE, lw=2.4,
            label='AI triage / document checking (emerging)')

    ax.axvline(4.6, color=AMBER, ls='--', lw=1.3)
    ax.text(4.6, 6.25, 'SAAS sits here:\nnew curve, not a better form',
            ha='center', va='center', fontsize=9.5, color=AMBER, fontweight='bold')
    ax.axhspan(0, 0.01, color='none')

    ax.text(6.9, 1.5, 'MVP deliberately\nexcludes AI', fontsize=9.5, color=BLUE,
            ha='center', va='center', fontweight='bold')
    ax.annotate('', xy=(6.2, 1.05), xytext=(7.4, 2.1),
                arrowprops=dict(arrowstyle='-|>', color=BLUE, lw=1.3))

    ax.set_xlabel('Effort / investment in the technology', fontsize=10, color=INK)
    ax.set_ylabel('Performance', fontsize=10, color=INK)
    ax.legend(loc='lower right', fontsize=9, frameon=False)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.tick_params(colors=GREY)
    ax.set_title('Figure 2 · S-curve: why SAAS is not "a better form"',
                 fontsize=11.5, fontweight='bold', color=INK, loc='left', pad=10)
    fig.tight_layout()
    p = os.path.join(OUT, 'fig2_scurve.png')
    fig.savefig(p, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return p


# ---------------------------------------------------------------- 圖三 互補品
def fig_complements():
    fig, ax = plt.subplots(figsize=(8.6, 5.4), dpi=170)
    base(ax, 10, 6.6)
    ax.text(0.3, 6.25, 'Figure 3 · Complementary assets SAAS depends on',
            fontsize=11.5, fontweight='bold', color=INK, va='center')

    box(ax, 5.0, 3.55, 3.5, 1.15, 'SAAS\nworkflow platform', BLUE, fs=11)

    parts = [
        (1.55, 5.35, 'Campus SSO\n(Entra ID)', GREEN),
        (5.0, 5.35, 'SIS / LMS\nassessment calendar', PURPLE),
        (8.45, 5.35, 'Email · Teams\npush delivery', AMBER),
        (1.55, 1.75, 'Registry-owned\npolicy config', RED),
        (5.0, 1.75, 'Document storage\n& retention policy', INK),
        (8.45, 1.75, 'Programme Office\nendorsement', GREEN),
    ]
    for x, y, t, c in parts:
        box(ax, x, y, 2.7, 0.95, t, c, fs=9.5)
        arrow(ax, x, y + (0.52 if y < 3.55 else -0.52), 5.0, 3.55 + (0.62 if y < 3.55 else -0.62),
              color='#C8C6C4', style='-|>', lw=1.2)

    ax.text(5.0, 0.45, 'Without these complements the product cannot create value —\n'
                       'the hard work is securing them, not writing the code.',
            ha='center', va='center', fontsize=9.5, color=GREY, linespacing=1.5)
    fig.tight_layout()
    p = os.path.join(OUT, 'fig3_complements.png')
    fig.savefig(p, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return p


if __name__ == '__main__':
    for f in (fig_workflow, fig_scurve, fig_complements):
        print('OK', f())
