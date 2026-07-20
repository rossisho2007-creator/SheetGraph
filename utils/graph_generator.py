"""
SheetGraph - Graph Generation Engine
Creates interactive Plotly visualizations
"""

import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import pandas as pd
from typing import Dict, Optional

# SheetGraph color palette
COLORS = ['#1B4332', '#2D6A4F', '#40916C', '#52B788', 
          '#74C69D', '#95D5B2', '#B7E4C7', '#D8F3DC']

def generate_graphs(df: pd.DataFrame) -> Dict[str, str]:
    """
    Generate comprehensive set of graphs from dataframe
    
    Args:
        df: Input dataframe
    
    Returns:
        Dictionary of graph names and HTML strings
    """
    graphs = {}
    
    # Get column types
    numeric_cols = df.select_dtypes(include=['number']).columns
    categorical_cols = df.select_dtypes(include=['object']).columns
    date_cols = df.select_dtypes(include=['datetime64']).columns
    
    # 1. Bar Chart - Categorical vs Numeric
    if len(categorical_cols) > 0 and len(numeric_cols) > 0:
        try:
            graphs['bar_chart'] = _create_bar_chart(df, categorical_cols[0], numeric_cols[0])
        except:
            pass
    
    # 2. Line Chart - Date vs Numeric
    if len(date_cols) > 0 and len(numeric_cols) > 0:
        try:
            graphs['line_chart'] = _create_line_chart(df, date_cols[0], numeric_cols[0])
        except:
            pass
    
    # 3. Histogram - Distribution
    if len(numeric_cols) > 0:
        try:
            graphs['histogram'] = _create_histogram(df, numeric_cols[0])
        except:
            pass
    
    # 4. Box Plot - Outliers
    if len(categorical_cols) > 0 and len(numeric_cols) > 0:
        try:
            graphs['box_plot'] = _create_box_plot(df, categorical_cols[0], numeric_cols[0])
        except:
            pass
    
    # 5. Pie Chart - Composition
    if len(categorical_cols) > 0:
        try:
            graphs['pie_chart'] = _create_pie_chart(df, categorical_cols[0])
        except:
            pass
    
    # 6. Scatter Plot - Correlation
    if len(numeric_cols) >= 2:
        try:
            graphs['scatter_plot'] = _create_scatter_plot(df, numeric_cols[0], numeric_cols[1])
        except:
            pass
    
    # 7. Correlation Heatmap
    if len(numeric_cols) >= 3:
        try:
            graphs['heatmap'] = _create_heatmap(df, numeric_cols)
        except:
            pass
    
    return graphs

def _create_bar_chart(df, cat_col, num_col):
    """Create horizontal bar chart"""
    # Get top 10 categories
    top_cats = df[cat_col].value_counts().head(10).index
    df_filtered = df[df[cat_col].isin(top_cats)]
    
    # Aggregate
    agg_data = df_filtered.groupby(cat_col)[num_col].sum().reset_index()
    agg_data = agg_data.sort_values(num_col, ascending=True)
    
    fig = px.bar(
        agg_data,
        x=num_col,
        y=cat_col,
        title=f'Total {num_col} by {cat_col}',
        orientation='h',
        color_discrete_sequence=[COLORS[2]]
    )
    
    fig.update_layout(
        template='plotly_white',
        height=400,
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis_title=num_col,
        yaxis_title=cat_col
    )
    
    return pio.to_html(fig, full_html=False)

def _create_line_chart(df, date_col, num_col):
    """Create line chart for time series"""
    df_sorted = df.sort_values(date_col)
    
    # Aggregate by date if needed
    daily_data = df_sorted.groupby(date_col)[num_col].sum().reset_index()
    
    fig = px.line(
        daily_data,
        x=date_col,
        y=num_col,
        title=f'{num_col} Over Time',
        color_discrete_sequence=[COLORS[1]]
    )
    
    fig.update_layout(
        template='plotly_white',
        height=400,
        margin=dict(l=20, r=20, t=40, b=20)
    )
    
    # Add markers
    fig.update_traces(mode='lines+markers')
    
    return pio.to_html(fig, full_html=False)

def _create_histogram(df, num_col):
    """Create histogram for distribution"""
    fig = px.histogram(
        df,
        x=num_col,
        title=f'Distribution of {num_col}',
        nbins=30,
        color_discrete_sequence=[COLORS[0]],
        marginal='box'
    )
    
    fig.update_layout(
        template='plotly_white',
        height=400,
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis_title=num_col,
        yaxis_title='Frequency'
    )
    
    return pio.to_html(fig, full_html=False)

def _create_box_plot(df, cat_col, num_col):
    """Create box plot for outlier detection"""
    # Limit categories for readability
    top_cats = df[cat_col].value_counts().head(8).index
    df_filtered = df[df[cat_col].isin(top_cats)]
    
    fig = px.box(
        df_filtered,
        x=cat_col,
        y=num_col,
        title=f'{num_col} Distribution by {cat_col}',
        color_discrete_sequence=[COLORS[3]]
    )
    
    fig.update_layout(
        template='plotly_white',
        height=400,
        margin=dict(l=20, r=20, t=40, b=20)
    )
    
    return pio.to_html(fig, full_html=False)

def _create_pie_chart(df, cat_col):
    """Create pie chart for composition"""
    value_counts = df[cat_col].value_counts().head(8)
    
    fig = px.pie(
        values=value_counts.values,
        names=value_counts.index,
        title=f'Composition of {cat_col}',
        color_discrete_sequence=COLORS[:len(value_counts)]
    )
    
    fig.update_layout(
        template='plotly_white',
        height=400,
        margin=dict(l=20, r=20, t=40, b=20)
    )
    
    fig.update_traces(textposition='inside', textinfo='percent+label')
    
    return pio.to_html(fig, full_html=False)

def _create_scatter_plot(df, x_col, y_col):
    """Create scatter plot with trendline"""
    fig = px.scatter(
        df,
        x=x_col,
        y=y_col,
        title=f'{y_col} vs {x_col}',
        trendline='ols',
        color_discrete_sequence=[COLORS[2]]
    )
    
    fig.update_layout(
        template='plotly_white',
        height=400,
        margin=dict(l=20, r=20, t=40, b=20)
    )
    
    return pio.to_html(fig, full_html=False)

def _create_heatmap(df, numeric_cols):
    """Create correlation heatmap"""
    corr_matrix = df[numeric_cols].corr()
    
    fig = go.Figure(data=go.Heatmap(
        z=corr_matrix.values,
        x=numeric_cols,
        y=numeric_cols,
        colorscale=[
            [0, '#D8F3DC'],
            [0.5, '#FFFFFF'],
            [1, '#1B4332']
        ],
        zmin=-1,
        zmax=1,
        text=corr_matrix.values.round(2),
        texttemplate='%{text}',
        textfont={"size": 10},
        hoverongaps=False
    ))
    
    fig.update_layout(
        title='Correlation Matrix',
        template='plotly_white',
        height=400,
        margin=dict(l=20, r=20, t=40, b=20)
    )
    
    return pio.to_html(fig, full_html=False)